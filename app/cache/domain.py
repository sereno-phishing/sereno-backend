"""Verdict cache: TTL settings, manual invalidations and cached verdicts.

Mirrors the extension's administration/domain/verdict-cache.js and the mock
backend's domainOf (platform/providers/mock.js)."""
import re
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from urllib.parse import urlsplit


VERDICTS = ("seguro", "advertencia", "bloqueo")
PHISHING = frozenset({"advertencia", "bloqueo"})
DEFAULT_TTL_PHISHING_DAYS = 7
DEFAULT_TTL_LEGIT_HOURS = 24
MAX_TTL_PHISHING_DAYS = 3650
MAX_TTL_LEGIT_HOURS = 87600
PENDING_REEVALUATION = "Se reevaluará en la próxima consulta"
REEVALUATED = "Reevaluado: "
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
    domain: str
    status: str
    date: str

    def public(self):
        return {"domain": self.domain, "status": self.status, "date": self.date}


@dataclass(frozen=True)
class CachedVerdict:
    status: str
    score: float
    cachedAt: datetime

    def __post_init__(self):
        if (self.status not in VERDICTS or type(self.score) not in (int, float)
                or not isinstance(self.cachedAt, datetime) or self.cachedAt.tzinfo is None):
            raise InvalidCache


@dataclass(frozen=True)
class CacheState:
    ttlPhishingDays: int = DEFAULT_TTL_PHISHING_DAYS
    ttlLegitHours: int = DEFAULT_TTL_LEGIT_HOURS
    invalidations: tuple = ()
    entries: dict = field(default_factory=dict)

    def __post_init__(self):
        if (not _positive(self.ttlPhishingDays, MAX_TTL_PHISHING_DAYS)
                or not _positive(self.ttlLegitHours, MAX_TTL_LEGIT_HOURS)):
            raise InvalidCache
        if any(not isinstance(item, Invalidation) for item in self.invalidations):
            raise InvalidCache
        if any(not isinstance(key, str) or not isinstance(value, CachedVerdict)
               for key, value in self.entries.items()):
            raise InvalidCache

    def public(self):
        return {"ttlPhishingDays": self.ttlPhishingDays, "ttlLegitHours": self.ttlLegitHours,
                "invalidations": [item.public() for item in self.invalidations]}

    def ttl_for(self, status):
        if status in PHISHING:
            return timedelta(days=self.ttlPhishingDays)
        return timedelta(hours=self.ttlLegitHours)

    def is_fresh(self, entry, now):
        return now - entry.cachedAt < self.ttl_for(entry.status)

    def with_ttl(self, days, hours):
        return replace(self, ttlPhishingDays=days, ttlLegitHours=hours)

    def with_invalidation(self, domain, day):
        entries = {key: value for key, value in self.entries.items() if key != domain}
        invalidation = Invalidation(domain, PENDING_REEVALUATION, day)
        return replace(self, invalidations=(invalidation, *self.invalidations), entries=entries)

    def with_entry(self, domain, entry, now):
        entries = {key: value for key, value in self.entries.items() if self.is_fresh(value, now)}
        entries[domain] = entry
        invalidations = tuple(
            Invalidation(item.domain, REEVALUATED + entry.status, item.date)
            if item.domain == domain and item.status == PENDING_REEVALUATION else item
            for item in self.invalidations)
        return replace(self, invalidations=invalidations, entries=entries)
