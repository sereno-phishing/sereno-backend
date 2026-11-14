import json
from contextlib import contextmanager
from datetime import datetime

import redis

from .domain import CachedVerdict, CacheState, CacheStorageError, InvalidCache, Invalidation


INDEX_PREFIX = "dominio:"
INVALIDATION_BATCH = 500


@contextmanager
def _redis():
    try:
        yield
    except redis.RedisError as failure:
        raise CacheStorageError("Cache unavailable") from failure


class RedisCacheRepository:
    """CacheRepository with the entries in Redis and the administration data
    (TTL settings, invalidations) in PostgreSQL.

    An entry lives under the hash of its URL as a JSON value {domain, verdict,
    score, cachedAt} and expires by itself after the TTL of its class. A set
    per domain ("dominio:<domain>") indexes the keys of that host, so an
    invalidation never scans the keyspace. A lookup still checks the entry
    against the current TTL settings: shortening a TTL takes effect at once on
    entries stored under the longer one. Either store being down raises
    CacheStorageError."""

    def __init__(self, client, database):
        """client: a redis.Redis created with decode_responses=True."""
        self.client = client
        self.database = database

    @staticmethod
    def _ttl(connection):
        row = connection.execute(
            "SELECT ttl_phishing_dias, ttl_legitima_horas FROM configuracion_cache "
            "WHERE id = 1").fetchone()
        return CacheState(*row) if row else CacheState()

    def _settings(self, connection):
        rows = connection.execute(
            "SELECT dominio, fecha, veredicto FROM invalidacion_cache "
            "ORDER BY id_invalidacion DESC").fetchall()
        state = self._ttl(connection)
        return CacheState(state.ttlPhishingDays, state.ttlLegitHours, tuple(
            Invalidation(domain, day.isoformat(), verdict) for domain, day, verdict in rows))

    def settings(self):
        with self.database.transaction(CacheStorageError) as connection:
            return self._settings(connection)

    def set_ttl(self, phishing_days, legit_hours):
        with self.database.transaction(CacheStorageError) as connection:
            connection.execute(
                "INSERT INTO configuracion_cache (id, ttl_phishing_dias, ttl_legitima_horas) "
                "VALUES (1, %s, %s) ON CONFLICT (id) DO UPDATE SET "
                "ttl_phishing_dias = EXCLUDED.ttl_phishing_dias, "
                "ttl_legitima_horas = EXCLUDED.ttl_legitima_horas", (phishing_days, legit_hours))
            return self._settings(connection)

    def invalidate(self, domain, day):
        index = INDEX_PREFIX + domain
        # The row is committed only after Redis dropped the entries: with
        # either store down, nothing is recorded and nothing is dropped.
        with self.database.transaction(CacheStorageError) as connection:
            connection.execute(
                "INSERT INTO invalidacion_cache (dominio, fecha) VALUES (%s, %s)", (domain, day))
            state = self._settings(connection)
            with _redis():
                # Popping in batches leaves no window where a key stored
                # meanwhile is dropped from the index but kept in the cache.
                while keys := self.client.spop(index, INVALIDATION_BATCH):
                    self.client.delete(*keys)
            return state

    def lookup(self, key, now):
        with _redis():
            value = self.client.get(key)
        if value is None:
            return None
        try:
            stored = json.loads(value)
            entry = CachedVerdict(stored["domain"], stored["verdict"], stored["score"],
                                  datetime.fromisoformat(stored["cachedAt"]))
        except (ValueError, KeyError, TypeError, InvalidCache) as error:
            raise CacheStorageError("Invalid cache entry") from error
        with self.database.transaction(CacheStorageError) as connection:
            return entry if self._ttl(connection).is_fresh(entry, now) else None

    def store(self, key, entry):
        value = json.dumps({"domain": entry.domain, "verdict": entry.verdict,
                            "score": entry.score, "cachedAt": entry.cachedAt.isoformat()})
        index = INDEX_PREFIX + entry.domain
        with self.database.transaction(CacheStorageError) as connection:
            seconds = int(self._ttl(connection).ttl_for(entry.verdict).total_seconds())
            with _redis():
                pipeline = self.client.pipeline(transaction=True)
                pipeline.set(key, value, ex=seconds)
                pipeline.sadd(index, key)
                # The index lives as long as its longest-lived entry.
                pipeline.expire(index, seconds, nx=True)
                pipeline.expire(index, seconds, gt=True)
                pipeline.execute()
            connection.execute(
                "UPDATE invalidacion_cache SET veredicto = %s "
                "WHERE dominio = %s AND veredicto IS NULL", (entry.verdict, entry.domain))
