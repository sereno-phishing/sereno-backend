from datetime import datetime, timedelta, timezone

import pytest

from app import create_app
from app.cache.domain import CacheStorageError, InvalidCache, domain_of
from app.cache.redis_repository import RedisCacheRepository
from app.cache.service import CacheService
from conftest import rows, set_role


START = datetime(2026, 10, 31, 9, 30, tzinfo=timezone(timedelta(hours=-5)))


class Clock:
    def __init__(self, value=START):
        self.value = value

    def __call__(self):
        return self.value

    def advance(self, **delta):
        self.value += timedelta(**delta)


@pytest.fixture
def repository(database, redis_client):
    return RedisCacheRepository(redis_client, database)


def stored(config):
    """-> [TTL settings, invalidations] as stored."""
    return rows(config, "configuracion_cache", "invalidacion_cache")


def make_admin(client, config, username="alice"):
    register(client, username)
    set_role(config, username, "admin")


def register(client, username="alice"):
    response = client.post("/v1/auth/register",
                           json={"username": username, "password": "throwaway"})
    assert response.status_code == 201
    return response


@pytest.mark.parametrize("url, expected", [
    ("https://www.Tienda-Servicios.pe/pago?x=1", "tienda-servicios.pe"),
    ("http://pago-servicios-linea.net:8080/", "pago-servicios-linea.net"),
    ("  paypal-secure-login.com  ", "paypal-secure-login.com"),
    ("WWW.example.com", "example.com"),
    ("about:blank", ""),
    ("", ""),
])
def test_domain_of_mirrors_extension_mock(url, expected):
    assert domain_of(url) == expected


def test_defaults_match_extension_seed(repository):
    assert CacheService(repository, Clock()).view() == {
        "ttlPhishingDays": 7, "ttlLegitHours": 24, "invalidations": []}


def test_ttl_is_validated_and_persisted(repository):
    service = CacheService(repository, Clock())
    assert service.set_ttl(3, 12)["ttlPhishingDays"] == 3
    assert service.view()["ttlLegitHours"] == 12
    for days, hours in [(0, 1), (1, -2), (True, 1), (1.5, 1), ("2", 1), (1, None), (5000, 1)]:
        with pytest.raises(InvalidCache):
            service.set_ttl(days, hours)
    assert service.view()["ttlPhishingDays"] == 3


def test_entries_expire_by_class_ttl(repository):
    clock = Clock()
    service = CacheService(repository, clock)
    service.store("key-safe", "safe.pe", "legitima", 0.05)
    service.store("key-bad", "bad.net", "phishing", 0.97)
    assert service.lookup("key-safe").score == 0.05
    clock.advance(hours=23, minutes=59)
    assert service.lookup("key-safe") is not None
    clock.advance(minutes=1)
    assert service.lookup("key-safe") is None
    found = service.lookup("key-bad")
    assert (found.domain, found.verdict) == ("bad.net", "phishing")
    clock.advance(days=6, minutes=1)
    assert service.lookup("key-bad") is None


def test_shorter_ttl_applies_to_existing_entries(repository):
    clock = Clock()
    service = CacheService(repository, clock)
    service.store("key-safe", "safe.pe", "legitima", 0.05)
    clock.advance(hours=2)
    service.set_ttl(7, 1)
    assert service.lookup("key-safe") is None


def test_invalidation_drops_every_entry_of_the_domain_and_is_listed_first(repository):
    clock = Clock()
    service = CacheService(repository, clock)
    service.store("key-bad-1", "bad.net", "phishing", 0.97)
    service.store("key-bad-2", "bad.net", "legitima", 0.2)
    service.store("key-other", "good.pe", "legitima", 0.1)
    service.invalidate("other.pe")
    view = service.invalidate(" https://www.Bad.net/x ")
    assert view["invalidations"][0] == {"domain": "bad.net", "date": "2026-10-31",
                                        "verdict": None}
    assert view["invalidations"][1]["domain"] == "other.pe"
    assert service.lookup("key-bad-1") is None and service.lookup("key-bad-2") is None
    assert service.lookup("key-other") is not None


def test_storing_a_new_verdict_reports_its_class_in_pending_invalidations(repository):
    service = CacheService(repository, Clock())
    service.invalidate("bad.net")
    service.invalidate("other.pe")
    service.store("key-bad", "bad.net", "phishing", 0.9)
    assert service.view()["invalidations"] == [
        {"domain": "other.pe", "date": "2026-10-31", "verdict": None},
        {"domain": "bad.net", "date": "2026-10-31", "verdict": "phishing"}]
    # Only the first re-evaluation after the invalidation is reported.
    service.store("key-bad-2", "bad.net", "legitima", 0.1)
    assert service.view()["invalidations"][1]["verdict"] == "phishing"


@pytest.mark.parametrize("verdict", ["seguro", "advertencia", "bloqueo", "", None])
def test_only_the_two_model_classes_are_cached(repository, verdict):
    service = CacheService(repository, Clock())
    with pytest.raises(InvalidCache):
        service.store("key", "shop.pe", verdict, 0.5)
    assert service.lookup("key") is None


@pytest.mark.parametrize("domain", ["", "   ", None, 5, "bad domain.pe", "a" * 254])
def test_invalid_invalidation_domain(repository, domain):
    with pytest.raises(InvalidCache):
        CacheService(repository, Clock()).invalidate(domain)


# HTTP

@pytest.mark.parametrize("method, path, body", [
    ("get", "/v1/cache", None),
    ("put", "/v1/cache/ttl", {"ttlPhishingDays": 3, "ttlLegitHours": 4}),
    ("post", "/v1/cache/invalidations", {"domain": "bad.net"}),
])
def test_cache_routes_require_administrator(config, method, path, body):
    client = create_app(config).test_client()
    kwargs = {} if body is None else {"json": body}
    before = stored(config)
    response = getattr(client, method)(path, **kwargs)
    assert response.status_code == 401
    assert response.get_json() == {"error": "invalid"}
    register(client)
    response = getattr(client, method)(path, **kwargs)
    assert response.status_code == 403
    assert response.get_json() == {"error": "forbidden"}
    assert stored(config) == before


def test_admin_reads_updates_and_invalidates_cache(config):
    client = create_app(config).test_client()
    make_admin(client, config)
    assert client.get("/v1/cache").get_json() == {"cache": {
        "ttlPhishingDays": 7, "ttlLegitHours": 24, "invalidations": []}}
    updated = client.put("/v1/cache/ttl", json={"ttlPhishingDays": 3, "ttlLegitHours": 12})
    assert updated.status_code == 200
    assert updated.get_json()["cache"]["ttlPhishingDays"] == 3
    invalidated = client.post("/v1/cache/invalidations", json={"domain": "Bad.net"})
    assert invalidated.status_code == 200
    entry = invalidated.get_json()["cache"]["invalidations"][0]
    assert set(entry) == {"domain", "date", "verdict"}
    assert entry["domain"] == "bad.net" and entry["verdict"] is None
    assert create_app(config).test_client().get("/v1/cache").status_code == 401


@pytest.mark.parametrize("path, body", [
    ("/v1/cache/ttl", {"ttlPhishingDays": 0, "ttlLegitHours": 4}),
    ("/v1/cache/ttl", {"ttlPhishingDays": 3}),
    ("/v1/cache/ttl", {"ttlPhishingDays": 3, "ttlLegitHours": 4, "x": 1}),
    ("/v1/cache/ttl", ["nope"]),
    ("/v1/cache/invalidations", {"domain": ""}),
    ("/v1/cache/invalidations", {"domain": "x", "extra": 1}),
    ("/v1/cache/invalidations", None),
])
def test_invalid_cache_input_is_rejected_without_changes(config, path, body):
    client = create_app(config).test_client()
    make_admin(client, config)
    before = stored(config)
    response = (client.put if path.endswith("ttl") else client.post)(path, json=body)
    assert response.status_code == 400
    assert response.get_json() == {"error": "invalid"}
    assert stored(config) == before




def test_settings_and_entries_are_shared_by_every_adapter_instance(repository, database,
                                                                   redis_client):
    service = CacheService(repository, Clock())
    service.set_ttl(2, 3)
    service.store("key-safe", "safe.pe", "legitima", 0.04)
    recreated = CacheService(RedisCacheRepository(redis_client, database), Clock())
    assert recreated.view()["ttlPhishingDays"] == 2
    assert recreated.lookup("key-safe").score == 0.04


@pytest.mark.parametrize("operation, method, path, body", [
    ("settings", "get", "/v1/cache", None),
    ("set_ttl", "put", "/v1/cache/ttl", {"ttlPhishingDays": 7, "ttlLegitHours": 24}),
    ("invalidate", "post", "/v1/cache/invalidations", {"domain": "bad.net"}),
])
def test_cache_storage_failure_reports_cache_down(config, monkeypatch, operation, method, path,
                                                  body):
    """The administration routes are about the cache itself: unlike the
    verdict route, they do not hide that it is down."""
    def fail(*arguments):
        raise CacheStorageError("Cache unavailable")

    client = create_app(config).test_client()
    make_admin(client, config)
    monkeypatch.setattr(RedisCacheRepository, operation, fail)
    response = getattr(client, method)(path, json=body)
    assert response.status_code == 503
    assert response.get_json() == {"error": "cache_down"}


def test_account_storage_failure_on_a_cache_route_is_a_server_error(config, database_down):
    client = create_app(config).test_client()
    make_admin(client, config)
    database_down()
    response = client.get("/v1/cache")
    assert response.status_code == 500
    assert response.get_json() == {"error": "server"}
