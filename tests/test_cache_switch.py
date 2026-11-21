"""The CACHE_ENABLED switch: on by default; off, every verdict comes from the
model and the cache is neither read nor written (response-time experiment,
run without cache). The response contract does not change."""
import logging

import pytest

from app import create_app
from app.config import settings
from app.verdict.classifier import Classification
from app.verdict.service import VerdictService


class SpyCache:
    """A cache service that would always hit, and records every call."""

    def __init__(self):
        self.calls = []

    def lookup(self, key):
        self.calls.append("lookup")
        raise AssertionError("a disabled cache must not be read")

    def store(self, *arguments):
        self.calls.append("store")
        raise AssertionError("a disabled cache must not be written")


class CountingClassifier:
    version = "9.9"

    def __init__(self):
        self.calls = 0

    def classify(self, url):
        self.calls += 1
        return Classification("phishing", 0.81)


class NullJournal:
    def record(self, username, served):
        pass


def test_the_cache_is_enabled_unless_cache_enabled_is_zero(monkeypatch):
    monkeypatch.delenv("CACHE_ENABLED", raising=False)
    assert settings()["CACHE_ENABLED"] is True
    monkeypatch.setenv("CACHE_ENABLED", "1")
    assert settings()["CACHE_ENABLED"] is True
    monkeypatch.setenv("CACHE_ENABLED", "0")
    assert settings()["CACHE_ENABLED"] is False


def test_the_cache_switch_rejects_non_boolean_config():
    # Checked before any storage is reached.
    with pytest.raises(ValueError, match="CACHE_ENABLED must be a boolean"):
        create_app({"TESTING": True, "CACHE_ENABLED": "0"})


def test_a_disabled_cache_is_neither_read_nor_written(caplog):
    cache, classifier = SpyCache(), CountingClassifier()
    service = VerdictService(cache, classifier, NullJournal(), cache_enabled=False)
    with caplog.at_level(logging.WARNING):
        answers = [service.evaluate("https://shop.pe/a", None) for _ in range(3)]
    assert [answer["source"] for answer in answers] == ["modelo"] * 3
    assert set(answers[0]) == {"domain", "verdict", "policy", "score", "source", "latencyMs"}
    assert (answers[0]["verdict"], answers[0]["score"]) == ("phishing", 0.81)
    assert classifier.calls == 3
    assert cache.calls == []
    # Not an outage: nothing is logged per request.
    assert not [record for record in caplog.records if "Cache unavailable" in record.getMessage()]


def test_with_the_cache_disabled_the_route_answers_from_the_model_and_caches_nothing(
        config, redis_client):
    client = create_app({**config, "CACHE_ENABLED": False}).test_client()
    url = "https://www.paypal-secure-login.com/x"
    first = client.post("/v1/verdict", json={"url": url}).get_json()
    second = client.post("/v1/verdict", json={"url": url}).get_json()
    assert set(first) == {"domain", "verdict", "policy", "score", "source", "latencyMs"}
    assert (first["source"], second["source"]) == ("modelo", "modelo")
    assert (second["verdict"], second["score"]) == ("phishing", 0.92)
    assert redis_client.keys("*") == []


def test_with_the_cache_enabled_the_route_still_caches(config):
    client = create_app({**config, "CACHE_ENABLED": True}).test_client()
    url = "https://www.paypal-secure-login.com/x"
    client.post("/v1/verdict", json={"url": url})
    assert client.post("/v1/verdict", json={"url": url}).get_json()["source"] == "cache"
