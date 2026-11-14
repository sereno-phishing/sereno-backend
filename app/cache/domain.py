"""Verdict cache: TTL settings, manual invalidations and cached verdicts.

Entries are keyed by the hash of the normalized URL and keep the model class
with the domain, never the URL. Settings and invalidations mirror the
extension's administration/domain/verdict-cache.js; domain_of mirrors the mock
backend's domainOf (platform/providers/mock.js)."""
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from urllib.parse import urlsplit


PHISHING = "phishing"
VERDICTS = (PHISHING, "legitima")  # the two classes of the model
DEFAULT_TTL_PHISHING_DAYS = 7
DEFAULT_TTL_LEGIT_HOURS = 24
MAX_TTL_PHISHING_DAYS = 3650
MAX_TTL_LEGIT_HOURS = 87600
_DOMAIN_LABEL = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\Z")
_SCHEME = re.compile(r"[a-zA-Z][a-zA-Z0-9+.-]*:")


class InvalidCache(Exception):
    pass


class CacheStorageError(Exception):
    pass


def domain_of(url):
    """Hostname of a URL, lowercased and without a leading "www."; a text that
    is not an absolute URL is taken as the host itself (like the mock)."""
    text = str(url or "").strip()
    host = text
    if _SCHEME.match(text):
        try:
            host = urlsplit(text).hostname or ""
        except ValueError:
            host = text
    host = host.lower()
    return host[4:] if host.startswith("www.") else host


def valid_domain(domain):
    return (isinstance(domain, str) and 0 < len(domain) <= 253
            and all(_DOMAIN_LABEL.fullmatch(label) for label in domain.split(".")))


def _positive(value, maximum):
    return type(value) is int and 0 < value <= maximum


@dataclass(frozen=True)
class Invalidation:
    """verdict is None until a URL of the domain is evaluated again, then the
    class the model gave it. The display text is the extension's concern."""
    domain: str
    date: str
    verdict: str | None = None

    def __post_init__(self):
        if (not isinstance(self.domain, str) or not isinstance(self.date, str)
                or not (self.verdict is None or self.verdict in VERDICTS)):
            raise InvalidCache

    def public(self):
        return {"domain": self.domain, "date": self.date, "verdict": self.verdict}


@dataclass(frozen=True)
class CachedVerdict:
    domain: str
    verdict: str
    score: float
    cachedAt: datetime

    def __post_init__(self):
        if (not isinstance(self.domain, str) or not self.domain
                or self.verdict not in VERDICTS or type(self.score) not in (int, float)
                or not isinstance(self.cachedAt, datetime) or self.cachedAt.tzinfo is None):
            raise InvalidCache


@dataclass(frozen=True)
class CacheState:
    """The administration view of the cache: the TTL of each class and the
    manual invalidations, newest first. The entries are not part of it."""

    ttlPhishingDays: int = DEFAULT_TTL_PHISHING_DAYS
    ttlLegitHours: int = DEFAULT_TTL_LEGIT_HOURS
    invalidations: tuple = ()

    def __post_init__(self):
        if (not _positive(self.ttlPhishingDays, MAX_TTL_PHISHING_DAYS)
                or not _positive(self.ttlLegitHours, MAX_TTL_LEGIT_HOURS)):
            raise InvalidCache
        if any(not isinstance(item, Invalidation) for item in self.invalidations):
            raise InvalidCache

    def public(self):
        return {"ttlPhishingDays": self.ttlPhishingDays, "ttlLegitHours": self.ttlLegitHours,
                "invalidations": [item.public() for item in self.invalidations]}

    def ttl_for(self, verdict):
        if verdict == PHISHING:
            return timedelta(days=self.ttlPhishingDays)
        return timedelta(hours=self.ttlLegitHours)

    def is_fresh(self, entry, now):
        return now - entry.cachedAt < self.ttl_for(entry.verdict)
