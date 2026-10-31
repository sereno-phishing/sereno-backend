from datetime import datetime

from ..json_file import JsonFile
from .domain import CachedVerdict, CacheState, CacheStorageError, InvalidCache, Invalidation


KEYS = {"ttlPhishingDays", "ttlLegitHours", "invalidations", "entries"}


def _state(data):
    if not isinstance(data, dict) or set(data) != KEYS:
        raise InvalidCache
    if not isinstance(data["invalidations"], list) or not isinstance(data["entries"], dict):
        raise InvalidCache
    invalidations = []
    for record in data["invalidations"]:
        if (not isinstance(record, dict) or set(record) != {"domain", "status", "date"}
                or any(not isinstance(value, str) for value in record.values())):
            raise InvalidCache
        invalidations.append(Invalidation(record["domain"], record["status"], record["date"]))
    entries = {}
    for domain, record in data["entries"].items():
        if (not isinstance(record, dict) or set(record) != {"status", "score", "cachedAt"}
                or not isinstance(record["cachedAt"], str)):
            raise InvalidCache
        entries[domain] = CachedVerdict(record["status"], record["score"],
                                        datetime.fromisoformat(record["cachedAt"]))
    return CacheState(data["ttlPhishingDays"], data["ttlLegitHours"],
                      tuple(invalidations), entries)


def _data(state):
    if not isinstance(state, CacheState):
        raise CacheStorageError("Invalid state")
    return {**state.public(), "entries": {
        domain: {"status": entry.status, "score": entry.score,
                 "cachedAt": entry.cachedAt.isoformat()}
        for domain, entry in state.entries.items()}}


class JsonCacheRepository:
    """JSON adapter of CacheRepository (single-process development storage)."""

    def __init__(self, path):
        self.file = JsonFile(path, _data(CacheState()), _state, CacheStorageError)

    def read(self):
        with self.file.lock:
            return _state(self.file.read())

    def update(self, change):
        def apply(data):
            state, result = change(_state(data))
            return _data(state), result
        return self.file.update(apply)
