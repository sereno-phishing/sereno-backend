import json
import os
import tempfile
from pathlib import Path
from threading import Lock, RLock

from .domain import AlreadySubmitted, InvalidSurvey, SurveyStorageError, SurveySubmission


_locks = {}
_locks_guard = Lock()


class JsonSurveyRepository:
    """Single-process storage; all instances share a lock per resolved path."""

    def __init__(self, path):
        self.path = Path(path).resolve()
        with _locks_guard:
            self.lock = _locks.setdefault(self.path, RLock())
        with self.lock:
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                if not self.path.exists():
                    self._write({"surveys": []})
                self._read()
            except OSError as error:
                raise SurveyStorageError("Storage unavailable") from error

    @staticmethod
    def _submission(record):
        if not isinstance(record, dict) or set(record) != {"answers", "submittedAt"}:
            raise InvalidSurvey
        answers = record["answers"]
        if not isinstance(answers, list):
            raise InvalidSurvey
        return SurveySubmission(tuple(answers), record["submittedAt"])

    @classmethod
    def _validate(cls, data):
        if not isinstance(data, dict) or set(data) not in ({"surveys"},
                                                           {"surveys", "participants"}):
            raise ValueError("Invalid schema")
        if not isinstance(data["surveys"], list):
            raise ValueError("Invalid collection")
        for record in data["surveys"]:
            cls._submission(record)
        if "participants" in data:
            participants = data["participants"]
            if (not isinstance(participants, list)
                    or any(not isinstance(username, str) or not username
                           for username in participants)
                    or participants != sorted(set(participants))):
                raise ValueError("Invalid participants")

    def _read(self):
        try:
            with self.path.open(encoding="utf-8") as source:
                data = json.load(source)
            self._validate(data)
            return data
        except (OSError, ValueError, TypeError, InvalidSurvey) as error:
            raise SurveyStorageError("Storage unavailable") from error

    def _write(self, data):
        temporary = None
        try:
            self._validate(data)
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=self.path.parent,
                                             prefix=self.path.name + ".", delete=False) as target:
                temporary = target.name
                json.dump(data, target, ensure_ascii=True, indent=2)
                target.flush()
                os.fsync(target.fileno())
            os.replace(temporary, self.path)
        except (OSError, ValueError, TypeError, InvalidSurvey) as error:
            raise SurveyStorageError("Storage unavailable") from error
        finally:
            if temporary is not None:
                try:
                    Path(temporary).unlink(missing_ok=True)
                except OSError:
                    pass

    @staticmethod
    def _valid_username(username):
        return isinstance(username, str) and bool(username)

    def submit(self, username, submission):
        if not self._valid_username(username) or not isinstance(submission, SurveySubmission):
            raise SurveyStorageError("Invalid submission")
        with self.lock:
            data = self._read()
            participants = data.get("participants", [])
            if username in participants:
                raise AlreadySubmitted
            data["surveys"].append({
                "answers": list(submission.answers),
                "submittedAt": submission.submittedAt,
            })
            data["participants"] = sorted([*participants, username])
            self._write(data)

    def has_submitted(self, username):
        if not self._valid_username(username):
            raise SurveyStorageError("Invalid username")
        with self.lock:
            return username in self._read().get("participants", [])

    def list_submissions(self):
        with self.lock:
            return tuple(self._submission(record) for record in self._read()["surveys"])
