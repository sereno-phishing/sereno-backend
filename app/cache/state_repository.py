class StateCacheRepository:
    """The CacheRepository operations for adapters that hold the whole
    CacheState and offer read() and an atomic update(change): the rules stay
    in the domain (CacheState), the adapter only loads and saves."""

    def settings(self):
        return self.read()

    def set_ttl(self, phishing_days, legit_hours):
        return self.update(lambda state: _both(state.with_ttl(phishing_days, legit_hours)))

    def invalidate(self, domain, day):
        return self.update(lambda state: _both(state.with_invalidation(domain, day)))

    def lookup(self, key, now):
        state = self.read()
        entry = state.entries.get(key)
        return entry if entry is not None and state.is_fresh(entry, now) else None

    def store(self, key, entry):
        self.update(lambda state: (state.with_entry(key, entry, entry.cachedAt), None))


def _both(state):
    return state, state
