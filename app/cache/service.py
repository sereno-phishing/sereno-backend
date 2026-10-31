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
        return self.repository.read().public()

    def set_ttl(self, phishing_days, legit_hours):
        for value, maximum in ((phishing_days, MAX_TTL_PHISHING_DAYS),
                               (legit_hours, MAX_TTL_LEGIT_HOURS)):
            if type(value) is not int or not 0 < value <= maximum:
                raise InvalidCache
        return self.repository.update(
            lambda state: self._public(state.with_ttl(phishing_days, legit_hours)))

    def invalidate(self, raw_domain):
        if not isinstance(raw_domain, str):
            raise InvalidCache
        domain = domain_of(raw_domain)
        if not valid_domain(domain):
            raise InvalidCache
        day = self.clock().strftime("%Y-%m-%d")
        return self.repository.update(
            lambda state: self._public(state.with_invalidation(domain, day)))

    def lookup(self, domain):
        """The cached verdict for the domain if it is still within its TTL, else None."""
        state = self.repository.read()
        entry = state.entries.get(domain)
        return entry if entry is not None and state.is_fresh(entry, self.clock()) else None

    def store(self, domain, status, score):
        now = self.clock()
        entry = CachedVerdict(status, score, now)
        self.repository.update(lambda state: (state.with_entry(domain, entry, now), None))

    @staticmethod
    def _public(state):
        return state, state.public()
