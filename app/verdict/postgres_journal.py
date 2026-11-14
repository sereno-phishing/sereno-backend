from datetime import datetime

from ..metrics.domain import QueryLogStorageError
from ..storage.consulta import insert_consulta


def local_now():
    return datetime.now().astimezone()


class PostgresVerdictJournal:
    """VerdictJournal over url_evaluada, consulta, prediccion and
    version_modelo: one transaction per served verdict. A cache hit has no
    prediccion row; a miss has one, tied to the version of the model that
    answered, which is registered the first time it is seen."""

    def __init__(self, database, clock=local_now):
        self.database = database
        self.clock = clock

    def record(self, username, served):
        now = self.clock()
        with self.database.transaction(QueryLogStorageError) as connection:
            consulta = insert_consulta(
                connection, username=username, url_hash=served.urlHash, domain=served.domain,
                verdict=served.verdict, policy=served.policy, score=served.score,
                source=served.source, latency_ms=served.latencyMs, at=now)
            if served.source != "modelo":
                return
            connection.execute(
                "INSERT INTO version_modelo (codigo, fecha) VALUES (%s, %s) "
                "ON CONFLICT (codigo) DO NOTHING", (served.modelVersion, now))
            connection.execute(
                "INSERT INTO prediccion (id_consulta, veredicto, puntaje, codigo_version, fecha) "
                "VALUES (%s, %s, %s, %s, %s)",
                (consulta, served.verdict, served.score, served.modelVersion, now))
