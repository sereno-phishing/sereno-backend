from datetime import datetime, timezone

from .domain import Session, StorageError, User


STAMP_FORMAT = "%Y-%m-%dT%H:%M"  # UTC, as the auth service writes it
_UPSERT_SESSION = """
    INSERT INTO sesion (token, id_usuario, fecha_inicio)
    SELECT %s, id_usuario, %s FROM usuario WHERE nombre = %s
    ON CONFLICT (token) DO UPDATE
        SET id_usuario = EXCLUDED.id_usuario, fecha_inicio = EXCLUDED.fecha_inicio"""


def _moment(stamp):
    try:
        return datetime.strptime(stamp, STAMP_FORMAT).replace(tzinfo=timezone.utc)
    except (TypeError, ValueError) as error:
        raise StorageError("Invalid timestamp") from error


def _stamp(moment):
    return moment.astimezone(timezone.utc).strftime(STAMP_FORMAT)


class PostgresAuthRepository:
    """AuthRepository over the usuario and sesion tables. Each operation is
    one transaction, so it is atomic across threads and processes."""

    def __init__(self, database):
        self.database = database

    def get_user(self, username):
        with self.database.transaction(StorageError) as connection:
            row = connection.execute(
                "SELECT nombre, hash_contrasena, rol, fecha_registro FROM usuario "
                "WHERE nombre = %s", (username,)).fetchone()
        return User(row[0], row[1], row[2], _stamp(row[3])) if row else None

    def register(self, user, session):
        created, login = _moment(user.createdAt), _moment(session.loginAt)
        with self.database.transaction(StorageError) as connection:
            # The unique name decides a concurrent race: the losing insert
            # waits for the winner and then inserts nothing.
            inserted = connection.execute(
                "INSERT INTO usuario (nombre, hash_contrasena, rol, fecha_registro) "
                "VALUES (%s, %s, %s, %s) ON CONFLICT (nombre) DO NOTHING",
                (user.username, user.password_hash, user.role, created)).rowcount
            if not inserted:
                return False
            if session.username != user.username:
                # Raising rolls the new user back with the rest of the transaction.
                raise StorageError("Initial session must belong to registered user")
            connection.execute(_UPSERT_SESSION, (session.token, login, session.username))
            return True

    def add_session(self, session):
        login = _moment(session.loginAt)
        with self.database.transaction(StorageError) as connection:
            stored = connection.execute(
                _UPSERT_SESSION, (session.token, login, session.username)).rowcount
            if not stored:
                raise StorageError("Session must reference an existing user")

    def get_session(self, token):
        with self.database.transaction(StorageError) as connection:
            row = connection.execute(
                "SELECT u.nombre, s.fecha_inicio, s.token FROM sesion s "
                "JOIN usuario u ON u.id_usuario = s.id_usuario WHERE s.token = %s",
                (token,)).fetchone()
        return Session(row[0], _stamp(row[1]), row[2]) if row else None

    def delete_session(self, token):
        with self.database.transaction(StorageError) as connection:
            connection.execute("DELETE FROM sesion WHERE token = %s", (token,))

    def list_users(self):
        with self.database.transaction(StorageError) as connection:
            rows = connection.execute(
                "SELECT nombre, hash_contrasena, rol, fecha_registro FROM usuario "
                'ORDER BY nombre COLLATE "C"').fetchall()
        return tuple(User(row[0], row[1], row[2], _stamp(row[3])) for row in rows)

    def set_role(self, username, role):
        # The NOT NULL and CHECK constraints of the column reject any other role.
        with self.database.transaction(StorageError) as connection:
            return connection.execute(
                "UPDATE usuario SET rol = %s WHERE nombre = %s", (role, username)).rowcount == 1

    def delete_user(self, username):
        """One statement, one transaction: the foreign keys remove the
        sessions and the survey participation mark of the user and set the
        user of its `consulta` rows to NULL (the rows stay, as when a history
        is cleared). The anonymous survey responses reference nobody and stay."""
        with self.database.transaction(StorageError) as connection:
            return connection.execute(
                "DELETE FROM usuario WHERE nombre = %s", (username,)).rowcount == 1
