from datetime import datetime

from ..json_file import JsonFile
from .domain import CachedVerdict, CacheState, CacheStorageError, InvalidCache, Invalidation


KEYS = {"ttlPhishingDays", "ttlLegitHours", "invalidations", "entries"}
ENTRY_KEYS = {"domain", "verdict", "score", "cachedAt"}
# Entries written before the two-class model: keyed by domain with a
# three-state status. They cannot be re-keyed by URL, so they are dropped.
LEGACY_ENTRY_KEYS = {"status", "score", "cachedAt"}


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
    for key, record in data["entries"].items():
        if isinstance(record, dict) and set(record) == LEGACY_ENTRY_KEYS:
            continue
        if (not isinstance(record, dict) or set(record) != ENTRY_KEYS
                or not isinstance(record["cachedAt"], str)):
            raise InvalidCache
        entries[key] = CachedVerdict(record["domain"], record["verdict"], record["score"],
                                     datetime.fromisoformat(record["cachedAt"]))
    return CacheState(data["ttlPhishingDays"], data["ttlLegitHours"],
                      tuple(invalidations), entries)


def _data(state):
    if not isinstance(state, CacheState):
        raise CacheStorageError("Invalid state")
    return {**state.public(), "entries": {
        key: {"domain": entry.domain, "verdict": entry.verdict, "score": entry.score,
              "cachedAt": entry.cachedAt.isoformat()}
        for key, entry in state.entries.items()}}


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
