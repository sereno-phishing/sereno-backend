"""What is specific to the Redis cache adapter; the behaviour of the cache
port is in tests/test_cache.py."""
from datetime import datetime, timedelta, timezone

import pytest

from app.cache.domain import CacheStorageError
from app.cache.service import CacheService


START = datetime(2026, 10, 31, 9, 30, tzinfo=timezone(timedelta(hours=-5)))
KEY = "a" * 64
OTHER_KEY = "b" * 64
DAY = 24 * 3600


@pytest.fixture
def repository(database, redis_client):
    from app.cache.redis_repository import RedisCacheRepository

    return RedisCacheRepository(redis_client, database)


@pytest.fixture
def service(repository):
    return CacheService(repository, lambda: START)


def test_entry_is_keyed_by_the_url_hash_and_expires_with_the_ttl_of_its_class(
        service, redis_client):
    service.store(KEY, "bad.net", "phishing", 0.97)
    service.store(OTHER_KEY, "safe.pe", "legitima", 0.05)
    assert set(redis_client.keys("*")) == {KEY, OTHER_KEY, "dominio:bad.net", "dominio:safe.pe"}
    assert 7 * DAY - 5 <= redis_client.ttl(KEY) <= 7 * DAY
    assert DAY - 5 <= redis_client.ttl(OTHER_KEY) <= DAY
    service.set_ttl(3, 12)
    service.store(KEY, "bad.net", "phishing", 0.97)
    service.store(OTHER_KEY, "safe.pe", "legitima", 0.05)
    assert 3 * DAY - 5 <= redis_client.ttl(KEY) <= 3 * DAY
    assert DAY // 2 - 5 <= redis_client.ttl(OTHER_KEY) <= DAY // 2


def test_entry_value_keeps_the_domain_the_class_the_score_and_the_date(service, redis_client):
    import json

    service.store(KEY, "bad.net", "phishing", 0.97)
    assert json.loads(redis_client.get(KEY)) == {
        "domain": "bad.net", "verdict": "phishing", "score": 0.97,
        "cachedAt": START.isoformat()}
    found = service.lookup(KEY)
    assert (found.domain, found.verdict, found.score, found.cachedAt) == (
        "bad.net", "phishing", 0.97, START)


def test_invalidation_uses_the_domain_index_and_leaves_other_domains(service, redis_client):
    service.store(KEY, "bad.net", "phishing", 0.97)
    service.store(OTHER_KEY, "bad.net", "legitima", 0.2)
    service.store("c" * 64, "good.pe", "legitima", 0.1)
    assert redis_client.smembers("dominio:bad.net") == {KEY, OTHER_KEY}
    # The index does not outlive the longest-lived entry it points at.
    assert 7 * DAY - 5 <= redis_client.ttl("dominio:bad.net") <= 7 * DAY
    service.invalidate("bad.net")
    assert set(redis_client.keys("*")) == {"c" * 64, "dominio:good.pe"}


def test_ttl_settings_and_invalidations_live_in_postgresql(service, database, redis_client):
    service.set_ttl(2, 3)
    service.invalidate("bad.net")
    service.store(KEY, "bad.net", "phishing", 0.9)
    with database.transaction(RuntimeError) as connection:
        assert connection.execute(
            "SELECT ttl_phishing_dias, ttl_legitima_horas FROM configuracion_cache"
        ).fetchall() == [(2, 3)]
        assert [(domain, day.isoformat(), verdict) for domain, day, verdict in connection.execute(
            "SELECT dominio, fecha, veredicto FROM invalidacion_cache")] == [
                ("bad.net", "2026-10-31", "phishing")]
    redis_client.flushdb()
    assert service.view() == {"ttlPhishingDays": 2, "ttlLegitHours": 3, "invalidations": [
        {"domain": "bad.net", "date": "2026-10-31", "verdict": "phishing"}]}


class Down:
    """A Redis client, or a Database, whose server cannot be reached."""

    def __getattr__(self, name):
        import redis

        def fail(*arguments, **keywords):
            raise redis.ConnectionError("down")
        return fail

    def transaction(self, error):
        raise error("Storage unavailable")


def test_redis_being_down_is_a_cache_storage_error(database):
    """Still the contract of the cache port after RF-20: the adapter reports
    the outage, the cache routes answer cache_down, and the verdict service
    is the one that falls back to the model (tests/test_verdict.py)."""
    from app.cache.redis_repository import RedisCacheRepository

    service = CacheService(RedisCacheRepository(Down(), database), lambda: START)
    with pytest.raises(CacheStorageError):
        service.lookup(KEY)
    with pytest.raises(CacheStorageError):
        service.store(KEY, "bad.net", "phishing", 0.9)
    with pytest.raises(CacheStorageError):
        service.invalidate("bad.net")
    # Nothing was recorded for the failed invalidation.
    assert service.view()["invalidations"] == []


def test_postgresql_being_down_is_a_cache_storage_error(repository, redis_client):
    from app.cache.redis_repository import RedisCacheRepository

    CacheService(repository, lambda: START).store(KEY, "bad.net", "phishing", 0.9)
    service = CacheService(RedisCacheRepository(redis_client, Down()), lambda: START)
    for action in (service.view, lambda: service.set_ttl(1, 1),
                   lambda: service.invalidate("bad.net"), lambda: service.lookup(KEY),
                   lambda: service.store(OTHER_KEY, "safe.pe", "legitima", 0.1)):
        with pytest.raises(CacheStorageError):
            action()
    assert redis_client.get(OTHER_KEY) is None


def test_an_unreadable_entry_fails_closed(service, redis_client):
    redis_client.set(KEY, "not json")
    with pytest.raises(CacheStorageError):
        service.lookup(KEY)
