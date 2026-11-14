"""The HTTP contract end to end on PostgreSQL and Redis, and what happens when
one of them cannot be reached."""
import pytest

from app import create_app
from app.storage.postgres import Database


SECRET_URL = "https://www.pago-servicios-linea.net/secret-path?token=abc"


def register(client, username="alice"):
    response = client.post("/v1/auth/register",
                           json={"username": username, "password": "throwaway"})
    assert response.status_code == 201
    return response


def make_admin(client, database, username="admin"):
    register(client, username)
    with database.transaction(RuntimeError) as connection:
        connection.execute("UPDATE usuario SET rol = 'admin' WHERE nombre = %s", (username,))


def test_accounts_sessions_and_duplicates(config):
    client = create_app(config).test_client()
    created = register(client).get_json()
    assert set(created["user"]) == {"username", "isAdmin", "createdAt"}
    assert client.post("/v1/auth/register", json={"username": "alice", "password": "x"}
                       ).status_code == 409
    # A new application instance sees the same account and session.
    again = create_app(config).test_client()
    again.set_cookie("sereno_session", created["token"])
    assert again.get("/v1/auth/session").get_json()["session"]["username"] == "alice"
    assert again.post("/v1/auth/login", json={"username": "alice", "password": "wrong"}
                      ).status_code == 401
    assert again.post("/v1/auth/login", json={"username": "alice", "password": "throwaway"}
                      ).status_code == 200
    assert client.post("/v1/auth/logout", json={}).status_code == 200
    assert client.get("/v1/auth/session").get_json() == {"session": None}


def test_verdicts_history_metrics_and_cache_administration(config, database):
    client = create_app(config).test_client()
    register(client)
    first = client.post("/v1/verdict", json={"url": SECRET_URL}).get_json()
    assert first == {"domain": "pago-servicios-linea.net", "verdict": "phishing",
                     "policy": "advertencia", "score": 0.97, "source": "modelo",
                     "latencyMs": first["latencyMs"]}
    assert client.post("/v1/verdict", json={"url": SECRET_URL}).get_json()["source"] == "cache"
    create_app(config).test_client().post("/v1/verdict", json={"url": "https://example.org/"})

    entries = client.get("/v1/history").get_json()["entries"]
    assert [(entry["domain"], entry["source"]) for entry in entries] == [
        ("pago-servicios-linea.net", "cache")]
    assert set(entries[0]) == {"id", "domain", "verdict", "policy", "score", "source", "ts",
                               "latencyMs"}

    admin = create_app(config).test_client()
    assert admin.get("/v1/metrics").status_code == 401
    make_admin(admin, database)
    assert client.get("/v1/metrics").status_code == 403
    metrics = admin.get("/v1/metrics").get_json()["metrics"]
    assert (metrics["analyzed"], metrics["phishingCount"], metrics["cacheHitPct"]) == (3, 2, 33)
    assert [query["source"] for query in metrics["queries"]] == ["Modelo", "Caché", "Modelo"]

    assert admin.get("/v1/cache").get_json() == {"cache": {
        "ttlPhishingDays": 7, "ttlLegitHours": 24, "invalidations": []}}
    assert admin.put("/v1/cache/ttl", json={"ttlPhishingDays": 3, "ttlLegitHours": 12}
                     ).get_json()["cache"]["ttlPhishingDays"] == 3
    invalidated = admin.post("/v1/cache/invalidations",
                             json={"domain": "pago-servicios-linea.net"}).get_json()["cache"]
    assert invalidated["invalidations"][0]["verdict"] is None
    assert client.post("/v1/verdict", json={"url": SECRET_URL}).get_json()["source"] == "modelo"
    assert admin.get("/v1/cache").get_json()["cache"]["invalidations"][0]["verdict"] == "phishing"

    assert client.delete("/v1/history").get_json() == {}
    assert client.get("/v1/history").get_json() == {"entries": []}
    assert admin.get("/v1/metrics").get_json()["metrics"]["analyzed"] == 4


def test_policies_and_survey(config, database):
    admin = create_app(config).test_client()
    make_admin(admin, database)
    rows = [{"domain": "pago-servicios-linea.net", "policy": "bloqueo",
             "updatedAt": "2026-11-13"}]
    assert admin.put("/v1/domains", json={"domains": rows}).get_json() == {"domains": rows}
    assert admin.get("/v1/domains").get_json() == {"domains": rows}
    assert admin.post("/v1/verdict", json={"url": SECRET_URL}).get_json()["policy"] == "bloqueo"

    answers = {"answers": [1, 2, 3, 4, 5, 1, 2, 3, 4, 5]}
    assert admin.get("/v1/survey").get_json() == {"submitted": False}
    assert admin.post("/v1/survey", json=answers).status_code == 200
    assert admin.post("/v1/survey", json=answers).get_json() == {"error": "already_submitted"}
    assert admin.get("/v1/survey").get_json() == {"submitted": True}


def test_no_url_reaches_a_table_or_a_redis_key(config, database, redis_client):
    client = create_app(config).test_client()
    register(client)
    client.post("/v1/verdict", json={"url": SECRET_URL})
    client.post("/v1/verdict", json={"url": SECRET_URL})
    with database.transaction(RuntimeError) as connection:
        tables = [row[0] for row in connection.execute(
            "SELECT tablename FROM pg_tables WHERE schemaname = 'public'").fetchall()]
        stored = repr([connection.execute(f'SELECT * FROM "{table}"').fetchall()
                       for table in tables])
    keys = redis_client.keys("*")
    cached = repr(keys) + repr([redis_client.get(key) for key in keys if len(key) == 64])
    for fragment in ("secret-path", "token=abc", "https://", "www."):
        assert fragment not in stored and fragment not in cached
    assert "pago-servicios-linea.net" in stored and "pago-servicios-linea.net" in cached


def redis_down(monkeypatch):
    import redis

    def down(*arguments, **keywords):
        raise redis.ConnectionError("down")
    monkeypatch.setattr(redis.Redis, "execute_command", down)
    monkeypatch.setattr(redis.client.Pipeline, "execute", down)


def test_redis_being_down_still_serves_the_verdict_with_the_model(config, monkeypatch, caplog):
    """RF-20 / CP-12. Before it, this request answered 503 cache_down."""
    client = create_app(config).test_client()
    register(client)
    redis_down(monkeypatch)
    with caplog.at_level("WARNING"):
        response = client.post("/v1/verdict", json={"url": SECRET_URL})
    assert response.status_code == 200
    body = response.get_json()
    assert set(body) == {"domain", "verdict", "policy", "score", "source", "latencyMs"}
    assert (body["domain"], body["verdict"], body["source"]) == (
        "pago-servicios-linea.net", "phishing", "modelo")
    warnings = [record.getMessage() for record in caplog.records
                if "Cache unavailable" in record.getMessage()]
    assert len(warnings) == 1
    assert "pago-servicios-linea.net" in warnings[0] and "secret-path" not in warnings[0]
    # The query is recorded as what it was: answered by the model, a MISS.
    [entry] = client.get("/v1/history").get_json()["entries"]
    assert entry["source"] == "modelo"
    with Database.shared(config["DATABASE_URL"]).transaction(RuntimeError) as connection:
        assert connection.execute(
            "SELECT origen, (SELECT count(*) FROM prediccion) FROM consulta"
        ).fetchall() == [("MISS", 1)]


def test_the_cache_is_used_again_once_redis_is_back(config, monkeypatch):
    """The same Redis client reconnects by itself, after the pause that
    follows a failure."""
    from app.verdict import service

    def sources(count):
        return [client.post("/v1/verdict", json={"url": SECRET_URL}).get_json()["source"]
                for _ in range(count)]

    now = [100.0]
    client = create_app(config).test_client()
    with monkeypatch.context() as outage:
        redis_down(outage)
        monkeypatch.setattr(service, "monotonic", lambda: now[0])
        assert sources(1) == ["modelo"]
    assert sources(2) == ["modelo", "modelo"]
    now[0] += service.CACHE_PAUSE_SECONDS
    assert sources(2) == ["modelo", "cache"]


def test_redis_being_down_still_reports_cache_down_on_the_cache_routes(
        config, database, monkeypatch):
    client = create_app(config).test_client()
    make_admin(client, database)
    redis_down(monkeypatch)
    response = client.post("/v1/cache/invalidations", json={"domain": "bad.net"})
    assert response.status_code == 503
    assert response.get_json() == {"error": "cache_down"}


def test_an_unreachable_database_prevents_startup(redis_url):
    from app.storage.postgres import DatabaseUnavailable

    with pytest.raises(DatabaseUnavailable):
        create_app({"TESTING": True, "REDIS_URL": redis_url,
                    "DATABASE_URL": "postgresql://sereno:sereno@127.0.0.1:1/sereno_test"})


def test_an_unreachable_redis_prevents_startup(database):
    from app.cache.domain import CacheStorageError

    with pytest.raises(CacheStorageError):
        create_app({"TESTING": True, "DATABASE_URL": database.url,
                    "REDIS_URL": "redis://127.0.0.1:1/15"})
