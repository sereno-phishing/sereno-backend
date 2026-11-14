from datetime import datetime, timedelta, timezone
from hashlib import sha256

import pytest

from app import create_app
from app.auth.domain import StorageError
from app.auth.postgres_repository import PostgresAuthRepository
from app.cache.redis_repository import RedisCacheRepository
from app.cache.service import CacheService
from app.history.domain import CAPACITY, HistoryRecord, HistoryStorageError, append
from app.history.service import HistoryService
from app.metrics.domain import QueryLogStorageError
from app.policy.domain import PolicyStorageError
from app.verdict.classifier import Classification
from app.verdict.fake_classifier import FakeClassifier
from app.verdict.postgres_journal import PostgresVerdictJournal
from app.verdict.service import ClassifierError, InvalidUrl, VerdictService
from app.verdict.url import cache_key, normalize
from conftest import set_role


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

    def __init__(self, result=Classification("phishing", 0.81)):
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


class StubPolicies:
    def __init__(self, policies=None, fail=False):
        self.policies = policies or {}
        self.fail = fail

    def policy_for(self, domain):
        if self.fail:
            raise PolicyStorageError("down")
        return self.policies.get(domain, "advertencia")


@pytest.fixture
def make_service(database, redis_client):
    """-> make(...) -> (verdict service, its cache service) on the test stores."""
    def make(classifier=None, history=None, clock=None, policies=None):
        clock = clock or Clock()
        cache = CacheService(RedisCacheRepository(redis_client, database), clock)
        return VerdictService(cache, classifier or FakeClassifier(), history or SpyHistory(),
                              timer=Timer(), policies=policies), cache
    return make


# FakeClassifier: two-class stand-in over the demo scenario domains of the extension

@pytest.mark.parametrize("url, verdict, score", [
    ("https://tienda-servicios.pe/", "legitima", 0.04),
    ("https://www.paypal-secure-login.com/login", "phishing", 0.92),
    ("http://pago-servicios-linea.net", "phishing", 0.97),
    ("https://reservas-hotel-lima.com/", "legitima", 0.05),
    ("https://seguro.localhost/", "legitima", 0.05),
    ("https://example.org/", "legitima", 0.05),
])
def test_fake_classifier_answers_the_two_model_classes(url, verdict, score):
    assert FakeClassifier().classify(url) == Classification(verdict, score)
    assert FakeClassifier.version == "1.3"


# URL normalization and cache key

def test_normalize_applies_rfc_3986_equivalences_and_keeps_distinct_urls_apart():
    assert normalize("  HTTPS://WWW.Shop.pe:443/a%7Eb?x=1#top ") == "https://www.shop.pe/a~b?x=1"
    assert normalize("https://shop.pe/a") != normalize("https://shop.pe/b")
    assert cache_key("https://shop.pe/a") == sha256(b"https://shop.pe/a").hexdigest()


def test_miss_uses_model_then_equivalent_url_hits_cache(make_service):
    classifier = SpyClassifier()
    service, _ = make_service(classifier)
    first = service.evaluate("https://www.Shop.pe/a", None)
    assert first == {"domain": "shop.pe", "verdict": "phishing", "policy": "advertencia",
                     "score": 0.81, "source": "modelo", "latencyMs": 42}
    second = service.evaluate("HTTPS://WWW.SHOP.PE:443/a#pay", None)
    assert second["source"] == "cache"
    assert second["verdict"] == "phishing" and second["score"] == 0.81
    assert classifier.calls == ["https://www.Shop.pe/a"]


def test_cache_is_keyed_by_url_not_by_domain(make_service):
    classifier = SpyClassifier()
    service, _ = make_service(classifier)
    service.evaluate("https://shop.pe/a", None)
    assert service.evaluate("https://shop.pe/b", None)["source"] == "modelo"
    assert len(classifier.calls) == 2


def test_expired_or_invalidated_entry_is_reclassified(make_service):
    clock = Clock()
    classifier = SpyClassifier(Classification("legitima", 0.1))
    service, cache = make_service(classifier, clock=clock)
    service.evaluate("https://shop.pe", None)
    clock.advance(hours=24)
    assert service.evaluate("https://shop.pe", None)["source"] == "modelo"
    cache.invalidate("shop.pe")
    assert service.evaluate("https://shop.pe", None)["source"] == "modelo"
    assert cache.view()["invalidations"][0] == {"domain": "shop.pe", "date": "2026-11-01",
                                                "verdict": "legitima"}
    assert len(classifier.calls) == 3


def test_invalidating_a_domain_drops_every_url_of_that_host_only(make_service):
    service, cache = make_service(SpyClassifier())
    for url in ("https://shop.pe/a", "https://www.shop.pe/b", "https://other.pe/a"):
        service.evaluate(url, None)
    cache.invalidate("shop.pe")
    assert service.evaluate("https://shop.pe/a", None)["source"] == "modelo"
    assert service.evaluate("https://www.shop.pe/b", None)["source"] == "modelo"
    assert service.evaluate("https://other.pe/a", None)["source"] == "cache"


def test_response_carries_the_domain_policy_in_force_even_on_a_cache_hit(make_service):
    policies = StubPolicies({"shop.pe": "bloqueo"})
    service, _ = make_service(SpyClassifier(), policies=policies)
    assert service.evaluate("https://shop.pe/a", None)["policy"] == "bloqueo"
    assert service.evaluate("https://other.pe/a", None)["policy"] == "advertencia"
    policies.policies["shop.pe"] = "advertencia"
    cached = service.evaluate("https://shop.pe/a", None)
    assert (cached["source"], cached["policy"]) == ("cache", "advertencia")


def test_policy_storage_failure_falls_back_to_the_default_policy(make_service):
    service, _ = make_service(SpyClassifier(), policies=StubPolicies(fail=True))
    verdict = service.evaluate("https://shop.pe/a", None)
    assert (verdict["verdict"], verdict["policy"]) == ("phishing", "advertencia")


def test_history_records_signed_in_users_only_and_never_fails_the_verdict(make_service):
    history = SpyHistory()
    service, _ = make_service(history=history)
    service.evaluate("https://shop.pe", None)
    verdict = service.evaluate("https://shop.pe", "alice")
    assert history.records == [("alice", verdict)]
    failing, _ = make_service(history=SpyHistory(fail=True))
    assert failing.evaluate("https://shop.pe", "alice")["verdict"] == "legitima"


@pytest.mark.parametrize("url", ["", "   ", "about:blank", "shop.pe", "ftp://shop.pe/x",
                                 "https://", "https://bad host.pe/", None, 3])
def test_url_the_normalizer_rejects_is_invalid(make_service, url):
    classifier = SpyClassifier()
    service, _ = make_service(classifier)
    with pytest.raises(InvalidUrl):
        service.evaluate(url, None)
    assert classifier.calls == []


@pytest.mark.parametrize("result", [RuntimeError("boom"), Classification("seguro", 0.5),
                                    Classification("phishing", 1.5), "phishing"])
def test_classifier_failure_or_bad_output_is_a_classifier_error(make_service, result):
    service, cache = make_service(SpyClassifier(result))
    with pytest.raises(ClassifierError):
        service.evaluate("https://shop.pe", None)
    assert cache.lookup(cache_key(normalize("https://shop.pe"))) is None


# History domain (mirrors Sereno.history.append)

def record(domain="shop.pe", verdict="legitima", ts="2026-10-31T09:30", id="h-1"):
    return HistoryRecord(id, domain, verdict, "advertencia", 0.1, "modelo", ts, 40)


def test_append_puts_newest_first_and_dedupes_same_visit_within_a_minute():
    entries = append((), record(id="h-1"))
    entries = append(entries, record(domain="other.pe", id="h-2"))
    entries = append(entries, record(ts="2026-10-31T09:31", id="h-3"))
    assert [entry.id for entry in entries] == ["h-3", "h-2"]
    entries = append(entries, record(ts="2026-10-31T09:33", id="h-4"))
    entries = append(entries, record(verdict="phishing", ts="2026-10-31T09:33", id="h-5"))
    assert [entry.id for entry in entries] == ["h-5", "h-4", "h-3", "h-2"]


def test_append_keeps_capacity_newest():
    entries = ()
    for index in range(CAPACITY + 5):
        entries = append(entries, record(domain=f"d{index}.pe", id=f"h-{index}"))
    assert len(entries) == CAPACITY
    assert entries[0].id == f"h-{CAPACITY + 4}"


def test_default_history_ids_follow_extension_format():
    ids = {HistoryService(None, Clock()).new_id() for _ in range(20)}
    assert len(ids) == 20
    assert all(value.startswith("h-") for value in ids)


# HTTP

def register(client, username="alice"):
    response = client.post("/v1/auth/register",
                           json={"username": username, "password": "throwaway"})
    assert response.status_code == 201


def test_verdict_route_returns_the_administrator_policy_of_the_domain(config):
    client = create_app(config).test_client()
    register(client)
    set_role(config, "alice", "admin")
    saved = client.put("/v1/domains", json={"domains": [
        {"domain": "paypal-secure-login.com", "policy": "bloqueo", "updatedAt": "2026-11-13"}]})
    assert saved.status_code == 200
    blocked = client.post("/v1/verdict", json={"url": "https://www.paypal-secure-login.com/x"})
    assert (blocked.get_json()["verdict"], blocked.get_json()["policy"]) == ("phishing", "bloqueo")
    other = client.post("/v1/verdict", json={"url": "https://example.org/"})
    assert (other.get_json()["verdict"], other.get_json()["policy"]) == ("legitima", "advertencia")


def test_injected_classifier_replaces_the_fake(config):
    client = create_app(config, classifier=SpyClassifier()).test_client()
    assert client.post("/v1/verdict", json={"url": "https://x.pe"}).get_json()["score"] == 0.81


@pytest.mark.parametrize("body", [None, [], {}, {"url": 5}, {"url": "https://x.pe", "extra": 1},
                                  {"url": "about:blank"}, {"url": "x.pe"},
                                  {"url": "ftp://x.pe/file"}])
def test_verdict_route_rejects_invalid_input(config, body):
    response = create_app(config).test_client().post("/v1/verdict", json=body)
    assert response.status_code == 400
    assert response.get_json() == {"error": "invalid"}


def test_verdict_route_reports_classifier_failure_as_server(config):
    client = create_app(config, classifier=SpyClassifier(RuntimeError("x"))).test_client()
    response = client.post("/v1/verdict", json={"url": "https://x.pe"})
    assert response.status_code == 500
    assert response.get_json() == {"error": "server"}




def test_verdict_route_classifies_caches_and_records_history(config):
    client = create_app(config).test_client()
    anonymous = client.post("/v1/verdict", json={"url": "https://www.paypal-secure-login.com/x"})
    assert anonymous.status_code == 200
    body = anonymous.get_json()
    assert set(body) == {"domain", "verdict", "policy", "score", "source", "latencyMs"}
    assert body["domain"] == "paypal-secure-login.com"
    assert (body["verdict"], body["policy"], body["score"], body["source"]) == (
        "phishing", "advertencia", 0.92, "modelo")
    assert isinstance(body["latencyMs"], int) and body["latencyMs"] >= 0

    register(client)
    # The anonymous verdict belongs to nobody.
    assert client.get("/v1/history").get_json() == {"entries": []}
    cached = client.post("/v1/verdict", json={"url": "https://www.paypal-secure-login.com/x"})
    assert cached.get_json()["source"] == "cache"
    entries = client.get("/v1/history").get_json()["entries"]
    assert [(entry["domain"], entry["verdict"], entry["policy"], entry["source"])
            for entry in entries] == [("paypal-secure-login.com", "phishing", "advertencia", "cache")]


def test_a_failure_recording_the_verdict_does_not_fail_it(config, monkeypatch):
    def fail(*arguments):
        raise QueryLogStorageError("Storage unavailable")

    client = create_app(config).test_client()
    register(client)
    monkeypatch.setattr(PostgresVerdictJournal, "record", fail)
    response = client.post("/v1/verdict", json={"url": "https://x.pe"})
    assert response.status_code == 200
    assert response.get_json()["verdict"] == "legitima"
    assert client.get("/v1/history").get_json() == {"entries": []}


def test_a_session_storage_failure_serves_the_verdict_as_anonymous(config, monkeypatch):
    def fail(*arguments):
        raise StorageError("Storage unavailable")

    client = create_app(config).test_client()
    register(client)
    monkeypatch.setattr(PostgresAuthRepository, "get_session", fail)
    response = client.post("/v1/verdict", json={"url": "https://x.pe"})
    assert response.status_code == 200
    monkeypatch.undo()
    assert client.get("/v1/history").get_json() == {"entries": []}


def test_an_unreachable_database_reports_cache_down(config, database_down):
    """The cache reads its TTL settings from PostgreSQL: without it no verdict
    can be cached or looked up."""
    client = create_app(config).test_client()
    database_down()
    response = client.post("/v1/verdict", json={"url": "https://x.pe"})
    assert response.status_code == 503
    assert response.get_json() == {"error": "cache_down"}
