from ..storage.consulta import SOURCE_OF_ORIGIN, insert_consulta
from .domain import CAPACITY, InvalidQueryEvent, QueryEvent, QueryLogStorageError


_LIST = """
    SELECT dominio, veredicto, politica, origen, latencia_ms, fecha FROM (
        SELECT c.id_consulta, u.dominio, c.veredicto, c.politica, c.origen, c.latencia_ms,
               c.fecha
        FROM consulta c JOIN url_evaluada u ON u.id_url = c.id_url
        ORDER BY c.id_consulta DESC LIMIT %s) newest
    ORDER BY id_consulta"""


class PostgresQueryLogRepository:
    """QueryLogRepository over every `consulta` row, with or without a user.
    Nothing is trimmed from the table; the log is its newest `capacity` rows."""

    def __init__(self, database, capacity=CAPACITY):
        self.database = database
        self.capacity = capacity

    def append(self, event):
        """An event that arrives through the port has no URL hash, score or
        user (the import of the JSON query log, tests). Verdicts served by the
        application are written by the verdict journal."""
        if not isinstance(event, QueryEvent):
            raise QueryLogStorageError("Invalid event")
        with self.database.transaction(QueryLogStorageError) as connection:
            insert_consulta(
                connection, username=None, url_hash=None, domain=event.domain,
                verdict=event.verdict, policy=event.policy, score=None, source=event.source,
                latency_ms=event.latencyMs, at=event.at)

    def list(self):
        with self.database.transaction(QueryLogStorageError) as connection:
            rows = connection.execute(_LIST, (self.capacity,)).fetchall()
        try:
            return tuple(QueryEvent(domain, verdict, policy, SOURCE_OF_ORIGIN[origin], latency, at)
                         for domain, verdict, policy, origin, latency, at in rows)
        except InvalidQueryEvent as error:
            raise QueryLogStorageError("Storage unavailable") from error
