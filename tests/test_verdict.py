from datetime import datetime, timedelta, timezone
from hashlib import sha256

import pytest

from app import create_app
from app.auth.domain import StorageError
from app.auth.postgres_repository import PostgresAuthRepository
from app.cache.domain import CacheStorageError
from app.cache.redis_repository import RedisCacheRepository
from app.cache.service import CacheService
from app.metrics.domain import QueryLogStorageError
from app.policy.domain import PolicyStorageError
from app.verdict.classifier import Classification
from app.verdict.fake_classifier import FakeClassifier
from app.verdict.journal import ServedVerdict
from app.verdict.postgres_journal import PostgresVerdictJournal
from app.verdict.service import (CACHE_PAUSE_SECONDS, ClassifierError, InvalidUrl,
                                 VerdictService)
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


class StubPolicies:
    def __init__(self, policies=None, fail=False):
        self.policies = policies or {}
        self.fail = fail

    def policy_for(self, domain):
        if self.fail:
            raise PolicyStorageError("down")
        return self.policies.get(domain, "advertencia")


class SpyJournal:
    def __init__(self, fail=False):
        self.fail = fail
        self.records = []

    def record(self, username, served):
        if self.fail:
            raise QueryLogStorageError("down")
        self.records.append((username, served))


class BrokenCache:
    """A cache service whose storage fails on the named operations."""

    def __init__(self, cache, *failing):
        self.cache = cache
        self.failing = failing
        self.calls = []

    def _call(self, name, *arguments):
        self.calls.append(name)
        if name in self.failing:
            raise CacheStorageError("Cache unavailable") from ConnectionError("redis refused")
        return getattr(self.cache, name)(*arguments)

    def lookup(self, key):
        return self._call("lookup", key)

    def store(self, *arguments):
        return self._call("store", *arguments)


def outage_warnings(caplog):
    return [record for record in caplog.records
            if record.name == "app.verdict.service" and "Cache unavailable" in record.getMessage()]


@pytest.fixture
def make_service(database, redis_client):
    """-> make(...) -> (verdict service, its cache service) on the test stores."""
    def make(classifier=None, journal=None, clock=None, policies=None):
        clock = clock or Clock()
        cache = CacheService(RedisCacheRepository(redis_client, database), clock)
        return VerdictService(cache, classifier or FakeClassifier(), journal or SpyJournal(),
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


# The design's conservative normalization (ADD, RNF-05): what is one URL for the cache.
@pytest.mark.parametrize("url, equivalent", [
    ("HTTPS://SHOP.PE/Pay", "https://shop.pe/Pay"),  # scheme and host lower-cased, path kept
    ("http://shop.pe:80/a", "http://shop.pe/a"),  # default http port dropped
    ("https://shop.pe:443/a", "https://shop.pe/a"),  # default https port dropped
    ("https://shop.pe/a#section", "https://shop.pe/a"),  # fragment dropped
    ("https://shop.pe/%7Euser/%41-%5F", "https://shop.pe/~user/A-_"),  # unreserved decoded
    ("https://shop.pe/a?q=%2E%30", "https://shop.pe/a?q=.0"),  # in the query too
])
def test_equivalent_urls_share_one_hash(url, equivalent):
    assert normalize(url) == normalize(equivalent)
    assert cache_key(normalize(url)) == cache_key(normalize(equivalent))


@pytest.mark.parametrize("url, other", [
    ("http://shop.pe/a", "https://shop.pe/a"),  # scheme
    ("https://www.shop.pe/a", "https://shop.pe/a"),  # "www."
    ("https://shop.pe/a/", "https://shop.pe/a"),  # trailing slash
    ("https://shop.pe/", "https://shop.pe"),  # trailing slash of the bare site
    ("https://shop.pe/a?x=1&y=2", "https://shop.pe/a?y=2&x=1"),  # query parameter order
    ("https://shop.pe:8443/a", "https://shop.pe/a"),  # a non-default port
    ("https://shop.pe/a%2Fb", "https://shop.pe/a/b"),  # a reserved encoding is not decoded
])
def test_urls_the_design_keeps_apart_get_different_hashes(url, other):
    assert cache_key(normalize(url)) != cache_key(normalize(other))


def test_the_cache_key_is_a_hex_sha256_that_does_not_contain_the_url():
    url = "https://shop.pe/pay?order=42"
    key = cache_key(normalize(url))
    assert len(key) == 64 and set(key) <= set("0123456789abcdef")
    assert "shop.pe" not in key


@pytest.mark.parametrize("url, other", [
    ("http://shop.pe/a", "https://shop.pe/a"),
    ("https://www.shop.pe/a", "https://shop.pe/a"),
    ("https://shop.pe/a/", "https://shop.pe/a"),
    ("https://shop.pe/a?x=1&y=2", "https://shop.pe/a?y=2&x=1"),
])
def test_distinct_urls_of_one_domain_are_cached_apart(make_service, url, other):
    classifier = SpyClassifier()
    service, _ = make_service(classifier)
    assert service.evaluate(url, None)["source"] == "modelo"
    assert service.evaluate(other, None)["source"] == "modelo"
    assert service.evaluate(url, None)["source"] == "cache"
    assert classifier.calls == [url, other]


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


def test_every_verdict_is_journaled_with_its_user_url_hash_and_model_version(make_service):
    journal = SpyJournal()
    service, _ = make_service(SpyClassifier(), journal=journal)
    miss = service.evaluate("https://shop.pe", None)
    hit = service.evaluate("https://shop.pe", "alice")
    key = cache_key(normalize("https://shop.pe"))
    assert journal.records == [
        (None, ServedVerdict(urlHash=key, modelVersion="9.9", **miss)),
        ("alice", ServedVerdict(urlHash=key, modelVersion=None, **hit))]
    failing, _ = make_service(journal=SpyJournal(fail=True))
    assert failing.evaluate("https://shop.pe/other", "alice")["verdict"] == "legitima"


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


# Cache outage (RF-20): the verdict is still served, by the model

OUTAGE_URL = "https://www.Shop.pe/secret-path?token=abc"


def test_a_cache_that_cannot_be_read_is_answered_by_the_model_and_logged_once(
        make_service, caplog):
    classifier, journal = SpyClassifier(), SpyJournal()
    service, cache = make_service(classifier, journal)
    service.cache = broken = BrokenCache(cache, "lookup", "store")
    with caplog.at_level("WARNING"):
        answer = service.evaluate(OUTAGE_URL, "alice")
    assert answer == {"domain": "shop.pe", "verdict": "phishing", "policy": "advertencia",
                      "score": 0.81, "source": "modelo", "latencyMs": 42}
    assert classifier.calls == [OUTAGE_URL]
    # No second attempt on a store that just failed.
    assert broken.calls == ["lookup"]
    key = cache_key(normalize(OUTAGE_URL))
    assert journal.records == [
        ("alice", ServedVerdict(urlHash=key, modelVersion="9.9", **answer))]
    [warning] = outage_warnings(caplog)
    assert warning.levelname == "WARNING"
    message = warning.getMessage()
    assert "read" in message and "shop.pe" in message and key in message
    assert "redis refused" in message
    for fragment in ("secret-path", "token=abc", "https://", "www."):
        assert fragment not in message


def test_a_cache_that_cannot_be_written_still_serves_the_verdict_and_is_logged_once(
        make_service, caplog):
    classifier, journal = SpyClassifier(), SpyJournal()
    service, cache = make_service(classifier, journal)
    service.cache = broken = BrokenCache(cache, "store")
    with caplog.at_level("WARNING"):
        answer = service.evaluate(OUTAGE_URL, None)
    assert (answer["verdict"], answer["score"], answer["source"]) == ("phishing", 0.81, "modelo")
    assert broken.calls == ["lookup", "store"]
    assert journal.records[0][1].modelVersion == "9.9"
    [warning] = outage_warnings(caplog)
    message = warning.getMessage()
    assert "write" in message and "shop.pe" in message and "secret-path" not in message
    # Nothing was cached: the next request asks the model again.
    assert service.evaluate(OUTAGE_URL, None)["source"] == "modelo"
    assert len(classifier.calls) == 2


def test_after_a_failure_the_cache_is_left_alone_for_a_while_then_tried_again(
        make_service, monkeypatch, caplog):
    """A stopped Redis can take seconds to fail: only one request per pause
    waits for it, the others go straight to the model."""
    now = [100.0]
    monkeypatch.setattr("app.verdict.service.monotonic", lambda: now[0])
    classifier = SpyClassifier()
    service, cache = make_service(classifier)
    service.cache = broken = BrokenCache(cache, "lookup")
    service.evaluate(OUTAGE_URL, None)
    now[0] += CACHE_PAUSE_SECONDS - 0.1
    caplog.clear()
    with caplog.at_level("WARNING"):
        paused = service.evaluate(OUTAGE_URL, None)
    assert paused["source"] == "modelo" and len(classifier.calls) == 2
    assert broken.calls == ["lookup"]
    [warning] = outage_warnings(caplog)
    assert "paused" in warning.getMessage() and "shop.pe" in warning.getMessage()
    now[0] += 0.1
    broken.failing = ()
    assert service.evaluate(OUTAGE_URL, None)["source"] == "modelo"
    assert broken.calls == ["lookup", "lookup", "store"]
    assert service.evaluate(OUTAGE_URL, None)["source"] == "cache"


SECRET_URL = "https://www.Shop.pe/reset?token=s3cr3t#frag"


def classifier_logs(caplog):
    return [record.getMessage() for record in caplog.records
            if record.name == "app.verdict.service" and "Classifier failed" in record.getMessage()]


def test_a_classifier_error_quoting_the_url_never_puts_it_in_the_log(make_service, caplog):
    # The model re-parses the URL and its errors quote it (InvalidUrlError embeds repr(url)).
    service, _ = make_service(SpyClassifier(ValueError(f"cannot score {SECRET_URL!r}")))
    with caplog.at_level("INFO"), pytest.raises(ClassifierError):
        service.evaluate(SECRET_URL, None)
    [message] = classifier_logs(caplog)
    assert "ValueError" in message and "shop.pe" in message
    assert cache_key(normalize(SECRET_URL)) in message
    logged = "\n".join(record.getMessage() for record in caplog.records)
    for fragment in ("reset", "token", "s3cr3t", "https://", "www.", "Shop.pe"):
        assert fragment not in logged


def test_an_unavailable_classifier_logs_its_own_message(make_service, caplog):
    from app.verdict.classifier import ClassifierUnavailable

    error = ClassifierUnavailable("Model artifact not found at 'model.joblib'.")
    service, _ = make_service(SpyClassifier(error))
    with caplog.at_level("INFO"), pytest.raises(ClassifierError):
        service.evaluate(SECRET_URL, None)
    [message] = classifier_logs(caplog)
    assert "Model artifact not found at 'model.joblib'." in message
    assert "s3cr3t" not in message


def test_model_unavailable_is_a_classifier_unavailable():
    from app.verdict.classifier import ClassifierUnavailable
    from app.verdict.model_classifier import ModelUnavailable

    assert issubclass(ModelUnavailable, ClassifierUnavailable)
    assert issubclass(ModelUnavailable, RuntimeError)


def test_a_classifier_failure_during_a_cache_outage_is_still_a_classifier_error(make_service):
    service, cache = make_service(SpyClassifier(RuntimeError("boom")))
    service.cache = BrokenCache(cache, "lookup", "store")
    with pytest.raises(ClassifierError):
        service.evaluate("https://shop.pe", None)


def test_a_working_cache_logs_no_outage(make_service, caplog):
    service, _ = make_service(SpyClassifier())
    with caplog.at_level("WARNING"):
        service.evaluate("https://shop.pe/a", None)
        service.evaluate("https://shop.pe/a", None)
    assert outage_warnings(caplog) == []


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


def test_an_unreachable_database_still_serves_the_verdict(config, database_down, caplog):
    """The cache reads its TTL settings from PostgreSQL: without it nothing
    can be cached, and neither the policy nor the query can be read or
    recorded, but the model still answers (this was 503 cache_down before RF-20)."""
    client = create_app(config).test_client()
    database_down()
    with caplog.at_level("WARNING"):
        response = client.post("/v1/verdict", json={"url": "https://x.pe"})
    assert response.status_code == 200
    body = response.get_json()
    assert (body["verdict"], body["policy"], body["source"]) == (
        "legitima", "advertencia", "modelo")
    [warning] = outage_warnings(caplog)
    assert "write" in warning.getMessage()
