from typing import Protocol

from .domain import HistoryRecord


class HistoryRepository(Protocol):
    """Port for per-user history, newest first: the same visit within the
    dedupe window is listed once and only the newest CAPACITY records are
    kept in the list. Storage failures raise HistoryStorageError."""

    def list(self, username: str) -> tuple[HistoryRecord, ...]: ...

    def clear(self, username: str) -> None: ...
