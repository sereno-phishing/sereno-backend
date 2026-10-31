import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app import create_app
from app.cache.domain import CacheStorageError, InvalidCache, domain_of
from app.cache.json_repository import JsonCacheRepository
from app.cache.memory_repository import MemoryCacheRepository
from app.cache.service import CacheService


START = datetime(2026, 10, 31, 9, 30, tzinfo=timezone(timedelta(hours=-5)))


class Clock:
    def __init__(self, value=START):
        self.value = value

    def __call__(self):
        return self.value

    def advance(self, **delta):
        self.value += timedelta(**delta)


@pytest.fixture
def config(tmp_path):
    return {"TESTING": True, "DATA_FILE": str(tmp_path / "auth.json"),
            "CACHE_DATA_FILE": str(tmp_path / "cache.json")}


def cache_path(config):
    return Path(config["CACHE_DATA_FILE"])


def register(client, username="alice"):
    response = client.post("/v1/auth/register",
                           json={"username": username, "password": "throwaway"})
    assert response.status_code == 201
    return response


def make_admin(client, config, username="alice"):
    register(client, username)
    path = Path(config["DATA_FILE"])
    data = json.loads(path.read_text(encoding="utf-8"))
    data["users"][username]["role"] = "admin"
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


@pytest.fixture(params=["memory", "json"])
def repository(request, tmp_path):
    if request.param == "memory":
        return MemoryCacheRepository()
    return JsonCacheRepository(tmp_path / "cache.json")


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


def test_entries_expire_by_status_ttl(repository):
    clock = Clock()
    service = CacheService(repository, clock)
    service.store("safe.pe", "seguro", 0.05)
    service.store("bad.net", "bloqueo", 0.97)
    assert service.lookup("safe.pe").score == 0.05
    clock.advance(hours=23, minutes=59)
    assert service.lookup("safe.pe") is not None
    clock.advance(minutes=1)
    assert service.lookup("safe.pe") is None
    assert service.lookup("bad.net").status == "bloqueo"
    clock.advance(days=6, minutes=1)
    assert service.lookup("bad.net") is None


def test_shorter_ttl_applies_to_existing_entries(repository):
    clock = Clock()
    service = CacheService(repository, clock)
    service.store("safe.pe", "seguro", 0.05)
    clock.advance(hours=2)
    service.set_ttl(7, 1)
    assert service.lookup("safe.pe") is None


def test_invalidation_drops_entry_and_is_listed_first(repository):
    clock = Clock()
    service = CacheService(repository, clock)
    service.store("bad.net", "bloqueo", 0.97)
    service.invalidate("other.pe")
    view = service.invalidate(" https://www.Bad.net/x ")
    assert view["invalidations"][0] == {
        "domain": "bad.net", "status": "Se reevaluará en la próxima consulta",
        "date": "2026-10-31"}
    assert view["invalidations"][1]["domain"] == "other.pe"
    assert service.lookup("bad.net") is None


def test_storing_a_new_verdict_marks_pending_invalidations_reevaluated(repository):
    service = CacheService(repository, Clock())
    service.invalidate("bad.net")
    service.store("bad.net", "advertencia", 0.9)
    assert service.view()["invalidations"][0]["status"] == "Reevaluado: advertencia"


@pytest.mark.parametrize("domain", ["", "   ", None, 5, "bad domain.pe", "a" * 254])
def test_invalid_invalidation_domain(repository, domain):
    with pytest.raises(InvalidCache):
        CacheService(repository, Clock()).invalidate(domain)


def test_json_repository_survives_recreation_and_fails_closed(tmp_path):
    path = tmp_path / "cache.json"
    service = CacheService(JsonCacheRepository(path), Clock())
    service.set_ttl(2, 3)
    service.store("safe.pe", "seguro", 0.04)
    recreated = CacheService(JsonCacheRepository(path), Clock())
    assert recreated.view()["ttlPhishingDays"] == 2
    assert recreated.lookup("safe.pe").score == 0.04
    path.write_text("{broken", encoding="utf-8")
    with pytest.raises(CacheStorageError):
        JsonCacheRepository(path)
    assert path.read_text(encoding="utf-8") == "{broken"


# HTTP

@pytest.mark.parametrize("method, path, body", [
    ("get", "/v1/cache", None),
    ("put", "/v1/cache/ttl", {"ttlPhishingDays": 3, "ttlLegitHours": 4}),
    ("post", "/v1/cache/invalidations", {"domain": "bad.net"}),
])
def test_cache_routes_require_administrator(config, method, path, body):
    client = create_app(config).test_client()
    kwargs = {} if body is None else {"json": body}
    before = cache_path(config).read_bytes()
    response = getattr(client, method)(path, **kwargs)
    assert response.status_code == 401
    assert response.get_json() == {"error": "invalid"}
    register(client)
    response = getattr(client, method)(path, **kwargs)
    assert response.status_code == 403
    assert response.get_json() == {"error": "forbidden"}
    assert cache_path(config).read_bytes() == before


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
    assert entry["domain"] == "bad.net"
    assert entry["status"] == "Se reevaluará en la próxima consulta"
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
    before = cache_path(config).read_bytes()
    response = (client.put if path.endswith("ttl") else client.post)(path, json=body)
    assert response.status_code == 400
    assert response.get_json() == {"error": "invalid"}
    assert cache_path(config).read_bytes() == before


def test_cache_storage_failure_reports_cache_down(config, monkeypatch):
    client = create_app(config).test_client()
    make_admin(client, config)
    cache_path(config).write_text("{broken", encoding="utf-8")
    response = client.get("/v1/cache")
    assert response.status_code == 503
    assert response.get_json() == {"error": "cache_down"}


def test_cache_file_defaults_beside_auth_data(tmp_path):
    create_app({"TESTING": True, "DATA_FILE": str(tmp_path / "auth.json")})
    assert (tmp_path / "cache.json").exists()
