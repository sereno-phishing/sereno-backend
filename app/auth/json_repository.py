import json
import os
import tempfile
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from threading import Lock, RLock

from .domain import ROLES, Session, StorageError, User


_locks = {}
_locks_guard = Lock()


class JsonAuthRepository:
    """Single-process storage; all instances share a lock per resolved path."""

    def __init__(self, path):
        self.path = Path(path).resolve()
        with _locks_guard:
            self.lock = _locks.setdefault(self.path, RLock())
        with self.lock:
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                if not self.path.exists():
                    self._write({"users": {}, "sessions": {}})
                self._read()
            except OSError as error:
                raise StorageError("Storage unavailable") from error

    @staticmethod
    def _validate(data):
        if not isinstance(data, dict) or set(data) != {"users", "sessions"}:
            raise ValueError("Invalid schema")
        users, sessions = data["users"], data["sessions"]
        if not isinstance(users, dict) or not isinstance(sessions, dict):
            raise ValueError("Invalid collections")
        for key, record in users.items():
            if not isinstance(record, dict) or set(record) != {
                "username", "password_hash", "role", "createdAt"
            }:
                raise ValueError("Invalid user")
            if not all(isinstance(value, str) and value for value in record.values()):
                raise ValueError("Invalid user fields")
            if (key != record["username"] or key != key.strip()
                    or record["role"] not in ROLES):
                raise ValueError("Invalid user identity")
            datetime.strptime(record["createdAt"], "%Y-%m-%dT%H:%M")
        for key, record in sessions.items():
            if not isinstance(record, dict) or set(record) != {"username", "loginAt", "token"}:
                raise ValueError("Invalid session")
            if not all(isinstance(value, str) and value for value in record.values()):
                raise ValueError("Invalid session fields")
            if key != record["token"] or record["username"] not in users:
                raise ValueError("Invalid session identity")
            datetime.strptime(record["loginAt"], "%Y-%m-%dT%H:%M")

    def _read(self):
        try:
            with self.path.open(encoding="utf-8") as source:
                data = json.load(source)
            self._validate(data)
            return data
        except (OSError, ValueError, TypeError) as error:
            raise StorageError("Storage unavailable") from error

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
        except (OSError, ValueError, TypeError) as error:
            raise StorageError("Storage unavailable") from error
        finally:
            if temporary is not None:
                try:
                    Path(temporary).unlink(missing_ok=True)
                except OSError:
                    pass

    def get_user(self, username):
        with self.lock:
            record = self._read()["users"].get(username)
            return User(**record) if record else None

    def register(self, user, session):
        with self.lock:
            data = self._read()
            if user.username in data["users"]:
                return False
            if session.username != user.username:
                raise StorageError("Initial session must belong to registered user")
            data["users"][user.username] = asdict(user)
            data["sessions"][session.token] = asdict(session)
            self._write(data)
            return True

    def add_session(self, session):
        with self.lock:
            data = self._read()
            data["sessions"][session.token] = asdict(session)
            self._write(data)

    def get_session(self, token):
        with self.lock:
            record = self._read()["sessions"].get(token)
            return Session(**record) if record else None

    def delete_session(self, token):
        with self.lock:
            data = self._read()
            if data["sessions"].pop(token, None) is not None:
                self._write(data)
