from typing import Any, Callable, Protocol

from .domain import CacheState


class CacheRepository(Protocol):
    """Port for the whole cache state; update is atomic within one process.
    Storage failures raise CacheStorageError (a Redis adapter replaces it later)."""

    def read(self) -> CacheState: ...

    def update(self, change: Callable[[CacheState], tuple[CacheState, Any]]) -> Any: ...
