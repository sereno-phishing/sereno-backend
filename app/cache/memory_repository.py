from threading import RLock

from .domain import CacheState, CacheStorageError


class MemoryCacheRepository:
    """Process-local test implementation of CacheRepository."""

    def __init__(self, state=None):
        self.state = state or CacheState()
        self.lock = RLock()

    def read(self):
        with self.lock:
            return self.state

    def update(self, change):
        with self.lock:
            state, result = change(self.state)
            if not isinstance(state, CacheState):
                raise CacheStorageError("Invalid state")
            self.state = state
            return result
