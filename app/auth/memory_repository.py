from dataclasses import replace
from threading import RLock

from .domain import Session, StorageError, User


class MemoryAuthRepository:
    """Process-local AuthRepository for tests; no persistence or runtime selection."""

    def __init__(self):
        self._users: dict[str, User] = {}
        self._sessions: dict[str, Session] = {}
        self._lock = RLock()

    def get_user(self, username: str) -> User | None:
        with self._lock:
            user = self._users.get(username)
            return replace(user) if user is not None else None

    def register(self, user: User, session: Session) -> bool:
        with self._lock:
            if user.username in self._users:
                return False
            if session.username != user.username:
                raise StorageError("Initial session must belong to registered user")
            self._users[user.username] = replace(user)
            self._sessions[session.token] = replace(session)
            return True

    def add_session(self, session: Session) -> None:
        with self._lock:
            if session.username not in self._users:
                raise StorageError("Session must reference an existing user")
            self._sessions[session.token] = replace(session)

    def get_session(self, token: str) -> Session | None:
        with self._lock:
            session = self._sessions.get(token)
            return replace(session) if session is not None else None

    def delete_session(self, token: str) -> None:
        with self._lock:
            self._sessions.pop(token, None)
