from typing import Protocol

from .domain import QueryEvent


class QueryLogRepository(Protocol):
    """Port for the bounded log of served verdicts, oldest first. Storage
    failures raise QueryLogStorageError."""

    def append(self, event: QueryEvent) -> None: ...

    def list(self) -> tuple[QueryEvent, ...]: ...
