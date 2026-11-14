from ..storage.consulta import SOURCE_OF_ORIGIN, insert_consulta
from .domain import (CAPACITY, DEDUPE_WINDOW_SECONDS, STAMP_FORMAT, HistoryRecord,
                     HistoryStorageError, InvalidHistory, parse_stamp)


# Every served verdict stays in `consulta` (the metrics count them all); the
# history rules decide what a user's list shows. A row is hidden when a later
# one of the same user repeats the visit (same domain, verdict and policy)
# within the dedupe window, compared at the minute resolution of the stamps,
# exactly as the domain's append() does; then the newest CAPACITY remain.
_LIST = f"""
    SELECT c.id_consulta, u.dominio, c.veredicto, c.politica, c.puntaje, c.origen, c.fecha,
           c.latencia_ms
    FROM consulta c JOIN url_evaluada u ON u.id_url = c.id_url
    WHERE c.id_usuario = (SELECT id_usuario FROM usuario WHERE nombre = %s)
      AND NOT EXISTS (
        SELECT 1 FROM consulta later JOIN url_evaluada lu ON lu.id_url = later.id_url
        WHERE later.id_usuario = c.id_usuario AND later.id_consulta > c.id_consulta
          AND lu.dominio = u.dominio AND later.veredicto = c.veredicto
          AND later.politica = c.politica
          AND abs(extract(epoch FROM date_trunc('minute', later.fecha)
                                     - date_trunc('minute', c.fecha)))
              <= {DEDUPE_WINDOW_SECONDS})
    ORDER BY c.id_consulta DESC LIMIT {CAPACITY}"""


class PostgresHistoryRepository:
    """HistoryRepository over the `consulta` rows that still belong to a user.

    zone: optional () -> tzinfo for the "YYYY-MM-DDTHH:MM" stamps; the
    system's local time zone by default, like the history service's clock.
    Record ids are derived from the row ("h-<id_consulta>")."""

    def __init__(self, database, zone=None):
        self.database = database
        self.zone = zone

    @staticmethod
    def _valid_username(username):
        if not isinstance(username, str) or not username:
            raise HistoryStorageError("Invalid username")

    def _stamp(self, moment):
        return moment.astimezone(self.zone() if self.zone else None).strftime(STAMP_FORMAT)

    def _moment(self, stamp):
        naive = parse_stamp(stamp)
        return naive.replace(tzinfo=self.zone()) if self.zone else naive.astimezone()

    def _records(self, connection, username):
        try:
            return tuple(
                HistoryRecord("h-" + str(row[0]), row[1], row[2], row[3], row[4],
                              SOURCE_OF_ORIGIN[row[5]], self._stamp(row[6]), row[7])
                for row in connection.execute(_LIST, (username,)).fetchall())
        except InvalidHistory as error:
            raise HistoryStorageError("Storage unavailable") from error

    def list(self, username):
        self._valid_username(username)
        with self.database.transaction(HistoryStorageError) as connection:
            return self._records(connection, username)

    def all(self):
        with self.database.transaction(HistoryStorageError) as connection:
            names = [row[0] for row in connection.execute(
                "SELECT nombre FROM usuario u WHERE EXISTS (SELECT 1 FROM consulta c "
                "WHERE c.id_usuario = u.id_usuario) ORDER BY nombre").fetchall()]
            return {name: self._records(connection, name) for name in names}

    def append(self, username, record):
        """A record that arrives through the port has no URL hash and no model
        version (the import of the JSON history, tests): it becomes a consulta
        row on the domain's hashless url_evaluada row, without a prediccion.
        Verdicts served by the application are written by the verdict journal."""
        self._valid_username(username)
        if not isinstance(record, HistoryRecord) or record.latencyMs is None:
            raise HistoryStorageError("Invalid record")
        with self.database.transaction(HistoryStorageError) as connection:
            if connection.execute("SELECT 1 FROM usuario WHERE nombre = %s",
                                  (username,)).fetchone() is None:
                raise HistoryStorageError("Unknown user")
            insert_consulta(
                connection, username=username, url_hash=None, domain=record.domain,
                verdict=record.verdict, policy=record.policy, score=record.score,
                source=record.source, latency_ms=record.latencyMs, at=self._moment(record.ts))

    def clear(self, username):
        """Unlinks the user from their queries instead of deleting them, so
        the metrics keep counting those verdicts. It cannot be undone."""
        self._valid_username(username)
        with self.database.transaction(HistoryStorageError) as connection:
            connection.execute(
                "UPDATE consulta SET id_usuario = NULL WHERE id_usuario = "
                "(SELECT id_usuario FROM usuario WHERE nombre = %s)", (username,))
