from typing import Protocol

from .domain import Session, User


class AuthRepository(Protocol):
    def get_user(self, username: str) -> User | None: ...

    def register(self, user: User, session: Session) -> bool:
        """Atomically create both records; return False on duplicate username."""
        ...

    def add_session(self, session: Session) -> None: ...

    def get_session(self, token: str) -> Session | None: ...

    def delete_session(self, token: str) -> None: ...
