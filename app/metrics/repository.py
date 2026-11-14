from typing import Protocol

from .domain import QueryEvent


class QueryLogRepository(Protocol):
    """Port for the bounded log of served verdicts (the newest CAPACITY),
    oldest first. Storage failures raise QueryLogStorageError."""

    def list(self) -> tuple[QueryEvent, ...]: ...
