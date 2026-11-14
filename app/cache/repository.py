from datetime import datetime
from typing import Protocol

from .domain import CachedVerdict, CacheState


class CacheRepository(Protocol):
    """Port for the verdict cache: TTL settings, manual invalidations and
    entries addressed one key at a time, so that a key-value store can serve
    it without ever reading every entry. Each operation is atomic. Storage
    failures raise CacheStorageError."""

    def settings(self) -> CacheState:
        """The TTL settings and the invalidations, newest first."""
        ...

    def set_ttl(self, phishing_days: int, legit_hours: int) -> CacheState:
        """-> the settings after the change. It applies to stored entries too."""
        ...

    def invalidate(self, domain: str, day: str) -> CacheState:
        """Drops every entry of the domain and lists the invalidation first
        -> the settings after the change."""
        ...

    def lookup(self, key: str, now: datetime) -> CachedVerdict | None:
        """The entry under the key if it is within the current TTL of its class at `now`."""
        ...

    def store(self, key: str, entry: CachedVerdict) -> None:
        """Keeps the entry and reports its class in the pending invalidations
        of its domain."""
        ...
