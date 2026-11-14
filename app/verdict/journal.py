"""Verdict journal port: one write per served verdict; the per-user
history and the metrics are two readings of those records."""
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class ServedVerdict:
    """What was answered, plus what only the verdict service knows: the hash
    of the normalized URL (never the URL) and, when the model answered, its
    version."""
    urlHash: str
    domain: str
    verdict: str
    policy: str
    score: float
    source: str  # "cache" | "modelo"
    latencyMs: int
    modelVersion: str | None = None


class VerdictJournal(Protocol):
    def record(self, username: str | None, served: ServedVerdict) -> None:
        """username is None for an anonymous verdict."""
        ...
