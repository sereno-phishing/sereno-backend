from typing import Protocol

from .domain import HistoryRecord


class HistoryRepository(Protocol):
    """Port for per-user history, newest first; operations are atomic within one
    process. Storage failures raise HistoryStorageError."""

    def list(self, username: str) -> tuple[HistoryRecord, ...]: ...

    def append(self, username: str, record: HistoryRecord) -> None:
        """Applies the domain append rule (dedupe, capacity)."""
        ...

    def clear(self, username: str) -> None: ...

    def all(self) -> dict[str, tuple[HistoryRecord, ...]]: ...
