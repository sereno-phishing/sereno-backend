from typing import Protocol

from .domain import Session, User


class AuthRepository(Protocol):
    """Port for valid domain records; operations are atomic within one process.

    Usernames are case-sensitive. Missing lookups return None and revocation is
    idempotent. Records cross the boundary as independent snapshots. Sessions
    reference existing users; invalid references raise StorageError without
    changing state. Multiple tokens per user are independent; writing an existing
    token replaces that session. Token uniqueness is the caller's responsibility.
    """

    def get_user(self, username: str) -> User | None: ...

    def register(self, user: User, session: Session) -> bool:
        """Create user and its own initial session; False on duplicate, no changes."""
        ...

    def add_session(self, session: Session) -> None: ...

    def get_session(self, token: str) -> Session | None: ...

    def delete_session(self, token: str) -> None: ...
