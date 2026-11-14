from ..storage.consulta import SOURCE_OF_ORIGIN
from .domain import (CAPACITY, DEDUPE_WINDOW_SECONDS, STAMP_FORMAT, HistoryRecord,
                     HistoryStorageError, InvalidHistory)


# Every served verdict stays in `consulta` (the metrics count them all); the
# history rules decide what a user's list shows. A row is hidden when a later
# one of the same user repeats the visit (same domain, verdict and policy)
# within the dedupe window, compared at the minute resolution of the stamps;
# then the newest CAPACITY remain.
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
    """HistoryRepository over the `consulta` rows that still belong to a user;
    the verdict journal writes them.

    zone: optional () -> tzinfo for the "YYYY-MM-DDTHH:MM" stamps; the
    system's local time zone by default.
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

    def list(self, username):
        self._valid_username(username)
        with self.database.transaction(HistoryStorageError) as connection:
            rows = connection.execute(_LIST, (username,)).fetchall()
        try:
            return tuple(
                HistoryRecord("h-" + str(row[0]), row[1], row[2], row[3], row[4],
                              SOURCE_OF_ORIGIN[row[5]], self._stamp(row[6]), row[7])
                for row in rows)
        except InvalidHistory as error:
            raise HistoryStorageError("Storage unavailable") from error

    def clear(self, username):
        """Unlinks the user from their queries instead of deleting them, so
        the metrics keep counting those verdicts. It cannot be undone."""
        self._valid_username(username)
        with self.database.transaction(HistoryStorageError) as connection:
            connection.execute(
                "UPDATE consulta SET id_usuario = NULL WHERE id_usuario = "
                "(SELECT id_usuario FROM usuario WHERE nombre = %s)", (username,))
