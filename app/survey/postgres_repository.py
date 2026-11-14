from datetime import datetime, timezone

from .domain import AlreadySubmitted, SurveyStorageError, SurveySubmission


STAMP_FORMAT = "%Y-%m-%dT%H:%M:%SZ"  # UTC, as the survey service writes it


class PostgresSurveyRepository:
    """SurveyRepository over encuesta_respuesta (anonymous answers) and
    encuesta_participante (which accounts took part). The two tables share no
    column, so a response cannot be traced to its participant."""

    def __init__(self, database):
        self.database = database

    @staticmethod
    def _valid_username(username):
        return isinstance(username, str) and bool(username)

    def submit(self, username, submission):
        if not self._valid_username(username) or not isinstance(submission, SurveySubmission):
            raise SurveyStorageError("Invalid submission")
        sent = datetime.strptime(submission.submittedAt, STAMP_FORMAT).replace(
            tzinfo=timezone.utc)
        with self.database.transaction(SurveyStorageError) as connection:
            account = connection.execute(
                "SELECT id_usuario FROM usuario WHERE nombre = %s", (username,)).fetchone()
            if account is None:
                raise SurveyStorageError("Unknown participant")
            # The primary key decides a concurrent race; raising rolls back,
            # so a rejected attempt leaves no response behind.
            marked = connection.execute(
                "INSERT INTO encuesta_participante (id_usuario) VALUES (%s) "
                "ON CONFLICT (id_usuario) DO NOTHING", (account[0],)).rowcount
            if not marked:
                raise AlreadySubmitted
            connection.execute(
                "INSERT INTO encuesta_respuesta (respuestas, fecha_envio) VALUES (%s, %s)",
                (list(submission.answers), sent))

    def has_submitted(self, username):
        if not self._valid_username(username):
            raise SurveyStorageError("Invalid username")
        with self.database.transaction(SurveyStorageError) as connection:
            return connection.execute(
                "SELECT 1 FROM encuesta_participante p "
                "JOIN usuario u ON u.id_usuario = p.id_usuario WHERE u.nombre = %s",
                (username,)).fetchone() is not None
