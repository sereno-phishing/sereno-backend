from datetime import datetime

from .domain import (MAX_TTL_LEGIT_HOURS, MAX_TTL_PHISHING_DAYS, CachedVerdict, InvalidCache,
                     domain_of, valid_domain)
from .repository import CacheRepository


def local_now():
    return datetime.now().astimezone()


class CacheService:
    def __init__(self, repository: CacheRepository, clock=local_now):
        self.repository = repository
        self.clock = clock

    def view(self):
        return self.repository.settings().public()

    def set_ttl(self, phishing_days, legit_hours):
        for value, maximum in ((phishing_days, MAX_TTL_PHISHING_DAYS),
                               (legit_hours, MAX_TTL_LEGIT_HOURS)):
            if type(value) is not int or not 0 < value <= maximum:
                raise InvalidCache
        return self.repository.set_ttl(phishing_days, legit_hours).public()

    def invalidate(self, raw_domain):
        """Drops every cached entry of the domain, whatever URL it was stored for."""
        if not isinstance(raw_domain, str):
            raise InvalidCache
        domain = domain_of(raw_domain)
        if not valid_domain(domain):
            raise InvalidCache
        day = self.clock().strftime("%Y-%m-%d")
        return self.repository.invalidate(domain, day).public()

    def lookup(self, key):
        """The entry cached under the key (the hash of a normalized URL) if it
        is still within the TTL of its class, else None."""
        return self.repository.lookup(key, self.clock())

    def store(self, key, domain, verdict, score):
        self.repository.store(key, CachedVerdict(domain, verdict, score, self.clock()))
