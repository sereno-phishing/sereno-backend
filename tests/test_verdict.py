import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app import create_app
from app.cache.memory_repository import MemoryCacheRepository
from app.cache.service import CacheService
from app.history.domain import CAPACITY, HistoryRecord, HistoryStorageError, append
from app.history.json_repository import JsonHistoryRepository
from app.history.service import HistoryService
from app.verdict.classifier import Classification
from app.verdict.fake_classifier import FakeClassifier
from app.verdict.service import ClassifierError, InvalidUrl, VerdictService


START = datetime(2026, 10, 31, 9, 30, tzinfo=timezone(timedelta(hours=-5)))


class Clock:
    def __init__(self, value=START):
        self.value = value

    def __call__(self):
        return self.value

    def advance(self, **delta):
        self.value += timedelta(**delta)


class Timer:
    """perf_counter fake: every call advances 0.0421 s."""

    def __init__(self):
        self.value = 0.0

    def __call__(self):
        self.value += 0.0421
        return self.value


class SpyClassifier:
    version = "9.9"

    def __init__(self, result=Classification("advertencia", 0.81)):
        self.result = result
        self.calls = []

    def classify(self, url):
        self.calls.append(url)
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


class SpyHistory:
    def __init__(self, fail=False):
        self.fail = fail
        self.records = []

    def record(self, username, verdict):
        if self.fail:
            raise HistoryStorageError("down")
        self.records.append((username, verdict))


def make_service(classifier=None, history=None, clock=None):
    clock = clock or Clock()
    cache = CacheService(MemoryCacheRepository(), clock)
    return VerdictService(cache, classifier or FakeClassifier(), history or SpyHistory(),
                          timer=Timer()), cache


# FakeClassifier reproduces the extension mock (demo scenarios + FALLBACK)

@pytest.mark.parametrize("url, status, score", [
    ("https://tienda-servicios.pe/", "seguro", 0.04),
    ("https://www.paypal-secure-login.com/login", "advertencia", 0.92),
    ("http://pago-servicios-linea.net", "bloqueo", 0.97),
    ("https://reservas-hotel-lima.com/", "seguro", 0.05),
    ("https://seguro.localhost/", "seguro", 0.05),
    ("https://example.org/", "seguro", 0.05),
])
def test_fake_classifier_mirrors_mock_scenarios(url, status, score):
    assert FakeClassifier().classify(url) == Classification(status, score)
    assert FakeClassifier.version == "1.3"


def test_miss_uses_model_then_hit_uses_cache():
    classifier = SpyClassifier()
    service, _ = make_service(classifier)
    first = service.evaluate("https://www.Shop.pe/a", None)
    assert first == {"domain": "shop.pe", "status": "advertencia", "score": 0.81,
                     "source": "modelo", "latencyMs": 42}
    second = service.evaluate("http://shop.pe/b", None)
    assert second["source"] == "cache"
    assert second["status"] == "advertencia" and second["score"] == 0.81
    assert classifier.calls == ["https://www.Shop.pe/a"]


def test_expired_or_invalidated_entry_is_reclassified():
    clock = Clock()
    classifier = SpyClassifier(Classification("seguro", 0.1))
    service, cache = make_service(classifier, clock=clock)
    service.evaluate("https://shop.pe", None)
    clock.advance(hours=24)
    assert service.evaluate("https://shop.pe", None)["source"] == "modelo"
    cache.invalidate("shop.pe")
    assert service.evaluate("https://shop.pe", None)["source"] == "modelo"
    assert cache.view()["invalidations"][0]["status"] == "Reevaluado: seguro"
    assert len(classifier.calls) == 3


def test_history_records_signed_in_users_only_and_never_fails_the_verdict():
    history = SpyHistory()
    service, _ = make_service(history=history)
    service.evaluate("https://shop.pe", None)
    verdict = service.evaluate("https://shop.pe", "alice")
    assert history.records == [("alice", verdict)]
    failing, _ = make_service(history=SpyHistory(fail=True))
    assert failing.evaluate("https://shop.pe", "alice")["status"] == "seguro"


@pytest.mark.parametrize("url", ["", "   ", "about:blank", None, 3])
def test_unusable_url_is_invalid(url):
    service, _ = make_service()
    with pytest.raises(InvalidUrl):
        service.evaluate(url, None)


@pytest.mark.parametrize("result", [RuntimeError("boom"), Classification("pendiente", 0.5),
                                    Classification("seguro", 1.5), "seguro"])
def test_classifier_failure_or_bad_output_is_a_classifier_error(result):
    service, cache = make_service(SpyClassifier(result))
    with pytest.raises(ClassifierError):
        service.evaluate("https://shop.pe", None)
    assert cache.lookup("shop.pe") is None


# History domain (mirrors Sereno.history.append)

def record(domain="shop.pe", status="seguro", ts="2026-10-31T09:30", id="h-1"):
    return HistoryRecord(id, domain, status, 0.1, "modelo", ts, 40)


def test_append_puts_newest_first_and_dedupes_same_visit_within_a_minute():
    entries = append((), record(id="h-1"))
    entries = append(entries, record(domain="other.pe", id="h-2"))
    entries = append(entries, record(ts="2026-10-31T09:31", id="h-3"))
    assert [entry.id for entry in entries] == ["h-3", "h-2"]
    entries = append(entries, record(ts="2026-10-31T09:33", id="h-4"))
    entries = append(entries, record(status="bloqueo", ts="2026-10-31T09:33", id="h-5"))
    assert [entry.id for entry in entries] == ["h-5", "h-4", "h-3", "h-2"]


def test_append_keeps_capacity_newest():
    entries = ()
    for index in range(CAPACITY + 5):
        entries = append(entries, record(domain=f"d{index}.pe", id=f"h-{index}"))
    assert len(entries) == CAPACITY
    assert entries[0].id == f"h-{CAPACITY + 4}"


def test_history_service_builds_extension_records(tmp_path):
    service = HistoryService(JsonHistoryRepository(tmp_path / "history.json"), Clock(),
                             id_factory=lambda: "h-fixed")
    service.record("alice", {"domain": "shop.pe", "status": "bloqueo", "score": 0.97,
                             "source": "cache", "latencyMs": 3})
    assert service.list("alice") == [{"id": "h-fixed", "domain": "shop.pe", "status": "bloqueo",
                                      "score": 0.97, "source": "cache",
                                      "ts": "2026-10-31T09:30", "latencyMs": 3}]
    assert service.list("bob") == []
    recreated = HistoryService(JsonHistoryRepository(tmp_path / "history.json"), Clock())
    assert recreated.list("alice")[0]["id"] == "h-fixed"


def test_default_history_ids_follow_extension_format():
    ids = {HistoryService(None, Clock()).new_id() for _ in range(20)}
    assert len(ids) == 20
    assert all(value.startswith("h-") for value in ids)


# HTTP

@pytest.fixture
def config(tmp_path):
    return {"TESTING": True, "DATA_FILE": str(tmp_path / "auth.json"),
            "CACHE_DATA_FILE": str(tmp_path / "cache.json"),
            "HISTORY_DATA_FILE": str(tmp_path / "history.json")}


def register(client, username="alice"):
    response = client.post("/v1/auth/register",
                           json={"username": username, "password": "throwaway"})
    assert response.status_code == 201


def stored_history(config):
    return json.loads(Path(config["HISTORY_DATA_FILE"]).read_text(encoding="utf-8"))["history"]


def test_verdict_route_classifies_caches_and_records_history(config):
    client = create_app(config).test_client()
    anonymous = client.post("/v1/verdict", json={"url": "https://www.paypal-secure-login.com/x"})
    assert anonymous.status_code == 200
    body = anonymous.get_json()
    assert set(body) == {"domain", "status", "score", "source", "latencyMs"}
    assert body["domain"] == "paypal-secure-login.com"
    assert (body["status"], body["score"], body["source"]) == ("advertencia", 0.92, "modelo")
    assert isinstance(body["latencyMs"], int) and body["latencyMs"] >= 0
    assert stored_history(config) == {}

    register(client)
    cached = client.post("/v1/verdict", json={"url": "https://paypal-secure-login.com"})
    assert cached.get_json()["source"] == "cache"
    entries = stored_history(config)["alice"]
    assert [(entry["domain"], entry["source"]) for entry in entries] == [
        ("paypal-secure-login.com", "cache")]


def test_injected_classifier_replaces_the_fake(config):
    client = create_app(config, classifier=SpyClassifier()).test_client()
    assert client.post("/v1/verdict", json={"url": "https://x.pe"}).get_json()["score"] == 0.81


@pytest.mark.parametrize("body", [None, [], {}, {"url": 5}, {"url": "x.pe", "extra": 1},
                                  {"url": "about:blank"}])
def test_verdict_route_rejects_invalid_input(config, body):
    response = create_app(config).test_client().post("/v1/verdict", json=body)
    assert response.status_code == 400
    assert response.get_json() == {"error": "invalid"}


def test_verdict_route_reports_cache_down(config):
    client = create_app(config).test_client()
    Path(config["CACHE_DATA_FILE"]).write_text("{broken", encoding="utf-8")
    response = client.post("/v1/verdict", json={"url": "https://x.pe"})
    assert response.status_code == 503
    assert response.get_json() == {"error": "cache_down"}


def test_verdict_route_reports_classifier_failure_as_server(config):
    client = create_app(config, classifier=SpyClassifier(RuntimeError("x"))).test_client()
    response = client.post("/v1/verdict", json={"url": "https://x.pe"})
    assert response.status_code == 500
    assert response.get_json() == {"error": "server"}


def test_history_failure_does_not_fail_the_verdict(config):
    client = create_app(config).test_client()
    register(client)
    Path(config["HISTORY_DATA_FILE"]).write_text("{broken", encoding="utf-8")
    response = client.post("/v1/verdict", json={"url": "https://x.pe"})
    assert response.status_code == 200
    assert response.get_json()["status"] == "seguro"
