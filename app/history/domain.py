"""Per-user history of evaluated sites. Mirrors the extension's
history/domain/history.js: the record shape, and the listing rules the
repository applies (the same visit within 60 s once, the 200 newest)."""
from dataclasses import dataclass
from datetime import datetime


DEDUPE_WINDOW_SECONDS = 60
CAPACITY = 200
STAMP_FORMAT = "%Y-%m-%dT%H:%M"
VERDICTS = frozenset({"phishing", "legitima"})
POLICIES = frozenset({"advertencia", "bloqueo"})
SOURCES = frozenset({"cache", "modelo"})
FIELDS = ("id", "domain", "verdict", "policy", "score", "source", "ts", "latencyMs")


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
    verdict: str  # the model class
    policy: str  # the domain policy in force when the verdict was served
    score: float
    source: str
    ts: str
    latencyMs: int | None

    def __post_init__(self):
        if (not isinstance(self.id, str) or not self.id or not isinstance(self.domain, str)
                or self.verdict not in VERDICTS or self.policy not in POLICIES
                or self.source not in SOURCES
                or type(self.score) not in (int, float) or parse_stamp(self.ts) is None
                or not (self.latencyMs is None or type(self.latencyMs) is int)):
            raise InvalidHistory

    def public(self):
        return {name: getattr(self, name) for name in FIELDS}
