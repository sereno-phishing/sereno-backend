"""Per-user history of evaluated sites. Mirrors the extension's
history/domain/history.js (record shape, 60 s dedupe, 200 newest)."""
from dataclasses import dataclass
from datetime import datetime


DEDUPE_WINDOW_SECONDS = 60
CAPACITY = 200
STAMP_FORMAT = "%Y-%m-%dT%H:%M"
STATUSES = frozenset({"seguro", "advertencia", "bloqueo"})
SOURCES = frozenset({"cache", "modelo"})
FIELDS = ("id", "domain", "status", "score", "source", "ts", "latencyMs")


class InvalidHistory(Exception):
    pass


class HistoryStorageError(Exception):
    pass


def parse_stamp(value):
    try:
        return datetime.strptime(value, STAMP_FORMAT)
    except (TypeError, ValueError):
        return None


@dataclass(frozen=True)
class HistoryRecord:
    id: str
    domain: str
    status: str
    score: float
    source: str
    ts: str
    latencyMs: int | None

    def __post_init__(self):
        if (not isinstance(self.id, str) or not self.id or not isinstance(self.domain, str)
                or self.status not in STATUSES or self.source not in SOURCES
                or type(self.score) not in (int, float) or parse_stamp(self.ts) is None
                or not (self.latencyMs is None or type(self.latencyMs) is int)):
            raise InvalidHistory

    @classmethod
    def from_public(cls, record):
        if not isinstance(record, dict) or set(record) != set(FIELDS):
            raise InvalidHistory
        return cls(*(record[name] for name in FIELDS))

    def public(self):
        return {name: getattr(self, name) for name in FIELDS}


def _duplicate(candidate, record):
    if candidate.domain != record.domain or candidate.status != record.status:
        return False
    delta = parse_stamp(candidate.ts) - parse_stamp(record.ts)
    return abs(delta.total_seconds()) <= DEDUPE_WINDOW_SECONDS


def append(entries, record):
    """Puts the record first, replacing the first duplicate visit, and keeps the
    newest CAPACITY entries. Returns a new tuple."""
    entries = list(entries)
    for index, candidate in enumerate(entries):
        if _duplicate(candidate, record):
            del entries[index]
            break
    return tuple([record, *entries][:CAPACITY])
