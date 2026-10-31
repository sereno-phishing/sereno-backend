import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app import create_app
from app.metrics.domain import CAPACITY, QueryEvent, compute_metrics
from app.metrics.json_repository import JsonQueryLogRepository
from app.metrics.service import MetricsService


LIMA = timezone(timedelta(hours=-5))
NOW = datetime(2026, 10, 31, 12, 0, tzinfo=LIMA)
SEED_KEYS = {"analyzed", "deltaPct", "phishingPct", "phishingCount", "latencyMs",
             "latencyTargetMs", "cacheHitPct", "sparklines", "hourly", "queries"}


def event(hours_ago, status="seguro", source="modelo", latency=100, domain="shop.pe"):
    return QueryEvent(domain, status, source, latency, NOW - timedelta(hours=hours_ago))


def test_empty_log_gives_zeroed_dashboard_shape():
    metrics = compute_metrics((), NOW)
    assert set(metrics) == SEED_KEYS
    assert metrics["analyzed"] == 0 and metrics["deltaPct"] == 0
    assert metrics["phishingPct"] == 0 and metrics["cacheHitPct"] == 0
    assert metrics["latencyMs"] == 0 and metrics["latencyTargetMs"] == 200
    assert set(metrics["sparklines"]) == {"indigo", "red", "amber", "green"}
    assert all(values == [0] * 7 for values in metrics["sparklines"].values())
    assert metrics["hourly"] == [{"level": "seguro", "height": 0}] * 24
    assert metrics["queries"] == []


def test_counts_rates_and_latency_over_the_last_24_hours():
    events = [
        event(1, "bloqueo", "cache", 40), event(2, "seguro", "modelo", 160),
        event(3, "advertencia", "modelo", 190), event(5, "seguro", "cache", 30),
        event(30, "seguro"), event(40, "seguro"),  # previous 24 h
        event(80, "bloqueo"),  # older, outside both windows
    ]
    metrics = compute_metrics(events, NOW)
    assert metrics["analyzed"] == 4
    assert metrics["deltaPct"] == 100
    assert metrics["phishingCount"] == 2
    assert metrics["phishingPct"] == 50.0
    assert metrics["latencyMs"] == 105
    assert metrics["cacheHitPct"] == 50


def test_hourly_bars_scale_to_chart_and_take_the_dominant_level():
    events = [event(0.5, "seguro"), event(0.6, "seguro"), event(0.7, "bloqueo"),
              event(1.5, "advertencia"), event(1.6, "bloqueo")]
    hourly = compute_metrics(events, NOW)["hourly"]
    assert len(hourly) == 24
    assert hourly[-1] == {"level": "seguro", "height": 99.0}
    assert hourly[-2] == {"level": "bloqueo", "height": 66.0}
    assert hourly[0] == {"level": "seguro", "height": 0}


def test_sparklines_are_seven_daily_bars_scaled_to_the_strip():
    events = [event(1), event(2), event(25, "bloqueo", "cache", 50)]
    sparklines = compute_metrics(events, NOW)["sparklines"]
    assert sparklines["indigo"] == [0, 0, 0, 0, 0, 16.2, 32.4]
    assert sparklines["red"] == [0, 0, 0, 0, 0, 32.4, 0]
    assert sparklines["amber"] == [0, 0, 0, 0, 0, 16.2, 32.4]
    assert sparklines["green"] == [0, 0, 0, 0, 0, 32.4, 0]


def test_latest_queries_are_newest_first_capped_and_formatted_for_the_table():
    events = [event(hours, domain=f"d{hours}.pe", source="cache" if hours % 2 else "modelo")
              for hours in range(60)]
    queries = compute_metrics(events, NOW)["queries"]
    assert len(queries) == 50
    assert queries[0] == {"domain": "d0.pe", "stamp": "31/10/2026 12:00", "source": "Modelo",
                          "latencyMs": 100, "status": "seguro"}
    assert queries[1]["source"] == "Caché"
    assert queries[1]["stamp"] == "31/10/2026 11:00"


def test_json_query_log_keeps_newest_capacity(tmp_path):
    assert CAPACITY >= 1000
    repository = JsonQueryLogRepository(tmp_path / "queries.json", capacity=5)
    service = MetricsService(repository, lambda: NOW)
    for index in range(8):
        repository.append(event(0, domain=f"d{index}.pe"))
    events = JsonQueryLogRepository(tmp_path / "queries.json", capacity=5).list()
    assert [item.domain for item in events] == [f"d{index}.pe" for index in range(3, 8)]
    assert service.metrics()["analyzed"] == 5


# HTTP

@pytest.fixture
def config(tmp_path):
    return {"TESTING": True, "DATA_FILE": str(tmp_path / "auth.json"),
            "QUERY_LOG_DATA_FILE": str(tmp_path / "queries.json")}


def register(client, username="alice"):
    response = client.post("/v1/auth/register",
                           json={"username": username, "password": "throwaway"})
    assert response.status_code == 201


def make_admin(client, config):
    register(client)
    path = Path(config["DATA_FILE"])
    data = json.loads(path.read_text(encoding="utf-8"))
    data["users"]["alice"]["role"] = "admin"
    path.write_text(json.dumps(data), encoding="utf-8")


def test_metrics_require_administrator(config):
    client = create_app(config).test_client()
    assert client.get("/v1/metrics").status_code == 401
    assert client.get("/v1/metrics").get_json() == {"error": "invalid"}
    register(client)
    assert client.get("/v1/metrics").status_code == 403
    assert client.get("/v1/metrics").get_json() == {"error": "forbidden"}


def test_metrics_reflect_every_verdict_including_anonymous(config):
    anonymous = create_app(config).test_client()
    anonymous.post("/v1/verdict", json={"url": "https://pago-servicios-linea.net/"})
    anonymous.post("/v1/verdict", json={"url": "https://pago-servicios-linea.net/"})
    anonymous.post("/v1/verdict", json={"url": "https://example.org/"})
    admin = create_app(config).test_client()
    make_admin(admin, config)
    metrics = admin.get("/v1/metrics").get_json()["metrics"]
    assert set(metrics) == SEED_KEYS
    assert metrics["analyzed"] == 3
    assert metrics["phishingCount"] == 2
    assert metrics["cacheHitPct"] == 33
    assert [query["source"] for query in metrics["queries"]] == ["Modelo", "Caché", "Modelo"]


def test_query_log_failure_never_fails_the_verdict_but_fails_metrics(config):
    client = create_app(config).test_client()
    make_admin(client, config)
    Path(config["QUERY_LOG_DATA_FILE"]).write_text("{broken", encoding="utf-8")
    assert client.post("/v1/verdict", json={"url": "https://x.pe"}).status_code == 200
    response = client.get("/v1/metrics")
    assert response.status_code == 500
    assert response.get_json() == {"error": "server"}


class VersionedClassifier:
    version = "2.0-stacking"

    def classify(self, url):
        raise AssertionError("not used")


def test_model_reports_injected_classifier_version_without_session(config):
    assert create_app(config).test_client().get("/v1/model").get_json() == {"version": "1.3"}
    client = create_app(config, classifier=VersionedClassifier()).test_client()
    assert client.get("/v1/model").get_json() == {"version": "2.0-stacking"}
