from .domain import InvalidPolicy, PolicyRow, PolicyStorageError


class PostgresPolicyRepository:
    """PolicyRepository over the politica_dominio table. The list keeps the
    order it was replaced with."""

    def __init__(self, database):
        self.database = database

    def list(self):
        with self.database.transaction(PolicyStorageError) as connection:
            rows = connection.execute(
                "SELECT dominio, politica, fecha_actualizacion FROM politica_dominio "
                "ORDER BY id_politica").fetchall()
        try:
            return tuple(PolicyRow.create(domain, policy, day.isoformat())
                         for domain, policy, day in rows)
        except InvalidPolicy as error:
            raise PolicyStorageError("Storage unavailable") from error

    def replace(self, rows):
        if (not isinstance(rows, tuple)
                or any(not isinstance(row, PolicyRow) for row in rows)):
            raise PolicyStorageError("Invalid policies")
        with self.database.transaction(PolicyStorageError) as connection:
            # Readers keep working; a concurrent replacement waits, so the
            # table always holds one complete list. A duplicated domain breaks
            # the unique constraint and the previous list is kept.
            connection.execute("LOCK TABLE politica_dominio IN EXCLUSIVE MODE")
            connection.execute("DELETE FROM politica_dominio")
            with connection.cursor() as cursor:
                cursor.executemany(
                    "INSERT INTO politica_dominio (dominio, politica, fecha_actualizacion) "
                    "VALUES (%s, %s, %s)",
                    [(row.domain, row.policy, row.updatedAt) for row in rows])
