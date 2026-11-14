import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from app import create_app
from app.config import settings
from app.history.domain import STAMP_FORMAT


@pytest.fixture
def json_config(tmp_path):
    return {**settings(), "TESTING": True, "STORAGE_DRIVER": "json",
            "DATA_FILE": str(tmp_path / "db.json")}


def run(json_config, database):
    from app.tools.import_json import import_json

    return import_json(json_config, database)


def rows(database, query):
    with database.transaction(RuntimeError) as connection:
        return connection.execute(query).fetchall()


def use_json_backend(json_config, tmp_path):
    """Fills the JSON files through the HTTP API, as local development did."""
    alice = create_app(json_config).test_client()
    alice.post("/v1/auth/register", json={"username": "alice", "password": "throwaway"})
    bob = create_app(json_config).test_client()
    token = bob.post("/v1/auth/register", json={"username": "bob", "password": "throwaway"}
                     ).get_json()["token"]
    path = tmp_path / "db.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["users"]["alice"]["role"] = "admin"
    path.write_text(json.dumps(data), encoding="utf-8")

    alice.put("/v1/domains", json={"domains": [
        {"domain": "pago-servicios-linea.net", "policy": "bloqueo", "updatedAt": "2026-11-13"}]})
    alice.put("/v1/cache/ttl", json={"ttlPhishingDays": 3, "ttlLegitHours": 12})
    alice.post("/v1/cache/invalidations", json={"domain": "old.pe"})
    alice.post("/v1/cache/invalidations", json={"domain": "tienda-servicios.pe"})
    bob.post("/v1/survey", json={"answers": [1, 2, 3, 4, 5, 1, 2, 3, 4, 5]})
    create_app(json_config).test_client().post(
        "/v1/verdict", json={"url": "https://example.org/anonymous"})
    bob.post("/v1/verdict", json={"url": "https://tienda-servicios.pe/"})
    bob.post("/v1/verdict", json={"url": "https://pago-servicios-linea.net/pay"})
    return token


def test_import_loads_every_json_file_and_reports_it(json_config, tmp_path, database):
    from app.auth.postgres_repository import PostgresAuthRepository
    from app.cache.redis_repository import RedisCacheRepository
    from app.history.postgres_repository import PostgresHistoryRepository
    from app.metrics.postgres_repository import PostgresQueryLogRepository
    from app.policy.postgres_repository import PostgresPolicyRepository
    from app.survey.postgres_repository import PostgresSurveyRepository

    token = use_json_backend(json_config, tmp_path)
    json_client = create_app(json_config).test_client()
    json_client.set_cookie("sereno_session", token)
    expected_history = json_client.get("/v1/history").get_json()["entries"]
    before = {path.name: path.read_bytes() for path in tmp_path.iterdir()}

    report = "\n".join(run(json_config, database))

    assert {path.name: path.read_bytes() for path in tmp_path.iterdir()} == before
    auth = PostgresAuthRepository(database)
    assert auth.get_user("alice").is_admin and not auth.get_user("bob").is_admin
    assert auth.get_user("bob").password_hash == json.loads(before["db.json"])[
        "users"]["bob"]["password_hash"]
    assert auth.get_session(token).username == "bob"
    assert [row.public() for row in PostgresPolicyRepository(database).list()] == [
        {"domain": "pago-servicios-linea.net", "policy": "bloqueo", "updatedAt": "2026-11-13"}]
    surveys = PostgresSurveyRepository(database)
    assert surveys.has_submitted("bob") and not surveys.has_submitted("alice")
    assert [item.answers for item in surveys.list_submissions()] == [
        (1, 2, 3, 4, 5, 1, 2, 3, 4, 5)]
    cache = RedisCacheRepository(None, database).settings().public()
    assert (cache["ttlPhishingDays"], cache["ttlLegitHours"]) == (3, 12)
    assert [(item["domain"], item["verdict"]) for item in cache["invalidations"]] == [
        ("tienda-servicios.pe", "legitima"), ("old.pe", None)]

    events = PostgresQueryLogRepository(database).list()
    assert [(item.domain, item.verdict, item.policy, item.source) for item in events] == [
        ("example.org", "legitima", "advertencia", "modelo"),
        ("tienda-servicios.pe", "legitima", "advertencia", "modelo"),
        ("pago-servicios-linea.net", "phishing", "bloqueo", "modelo")]
    history = PostgresHistoryRepository(database).list("bob")
    without_id = [{name: value for name, value in item.public().items() if name != "id"}
                  for item in history]
    assert without_id == [{name: value for name, value in entry.items() if name != "id"}
                          for entry in expected_history]
    # The history records were paired with their query-log events: no double count.
    assert rows(database, "SELECT count(*) FROM consulta") == [(3,)]
    assert rows(database, "SELECT count(*) FROM url_evaluada WHERE hash_url IS NOT NULL") == [
        (0,)]
    assert rows(database, "SELECT count(*) FROM prediccion") == [(0,)]
    for expected in ("2 imported", "bob", "URL hash", "cache entries"):
        assert expected in report


def test_import_is_idempotent(json_config, tmp_path, database):
    use_json_backend(json_config, tmp_path)
    run(json_config, database)
    counts = "SELECT (SELECT count(*) FROM usuario), (SELECT count(*) FROM sesion), " \
             "(SELECT count(*) FROM politica_dominio), (SELECT count(*) FROM encuesta_respuesta), " \
             "(SELECT count(*) FROM invalidacion_cache), (SELECT count(*) FROM consulta)"
    first = rows(database, counts)
    report = "\n".join(run(json_config, database))
    assert rows(database, counts) == first == [(2, 2, 1, 1, 2, 3)]
    assert report.count("already imported") == 5


def test_import_keeps_what_postgresql_already_has(json_config, tmp_path, database):
    from app.auth.domain import Session, User
    from app.auth.postgres_repository import PostgresAuthRepository
    from app.policy.domain import PolicyRow
    from app.policy.postgres_repository import PostgresPolicyRepository

    token = use_json_backend(json_config, tmp_path)
    auth = PostgresAuthRepository(database)
    auth.register(User("bob", "another-hash", "user", "2026-11-01T10:00"),
                  Session("bob", "2026-11-01T10:00", "postgres-token"))
    PostgresPolicyRepository(database).replace((
        PolicyRow.create("pago-servicios-linea.net", "advertencia", "2026-11-01"),))

    report = "\n".join(run(json_config, database))

    assert auth.get_user("bob").password_hash == "another-hash"
    # A session of the JSON account must not open the PostgreSQL account of the same name.
    assert auth.get_session(token) is None
    assert PostgresPolicyRepository(database).list()[0].policy == "advertencia"
    assert rows(database, "SELECT count(*) FROM consulta WHERE id_usuario IS NOT NULL") == [(0,)]
    assert "bob" in report and "pago-servicios-linea.net" in report


def test_history_without_a_query_log_event_is_added_in_time_order(json_config, tmp_path,
                                                                  database):
    from app.history.postgres_repository import PostgresHistoryRepository

    use_json_backend(json_config, tmp_path)
    path = tmp_path / "history.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    oldest = datetime.strptime(data["history"]["bob"][-1]["ts"], STAMP_FORMAT)
    data["history"]["bob"].append({
        "id": "h-old", "domain": "antiguo.pe", "status": "bloqueo", "score": 0.9,
        "source": "cache", "ts": (oldest - timedelta(days=2)).strftime(STAMP_FORMAT),
        "latencyMs": 5})
    path.write_text(json.dumps(data), encoding="utf-8")

    report = "\n".join(run(json_config, database))

    history = PostgresHistoryRepository(database).list("bob")
    assert [item.domain for item in history] == [
        "pago-servicios-linea.net", "tienda-servicios.pe", "antiguo.pe"]
    assert (history[-1].verdict, history[-1].policy, history[-1].source) == (
        "phishing", "bloqueo", "cache")
    assert rows(database, "SELECT count(*) FROM consulta") == [(4,)]
    assert "1 without a query-log event" in report


def test_missing_files_are_reported_and_never_created(json_config, tmp_path, database):
    report = "\n".join(run(json_config, database))
    assert list(tmp_path.iterdir()) == []
    assert report.count("not found") == 5
    assert rows(database, "SELECT count(*) FROM usuario") == [(0,)]
    # Nothing was marked as imported: a later run with the files still loads them.
    use_json_backend(json_config, tmp_path)
    run(json_config, database)
    assert rows(database, "SELECT count(*) FROM usuario") == [(2,)]


def test_a_damaged_file_is_reported_and_the_other_sections_still_load(json_config, tmp_path,
                                                                     database):
    use_json_backend(json_config, tmp_path)
    Path(tmp_path / "policies.json").write_text("{broken", encoding="utf-8")
    report = "\n".join(run(json_config, database))
    assert "policies.json" in report and "could not be read" in report
    assert rows(database, "SELECT count(*) FROM usuario") == [(2,)]
    assert rows(database, "SELECT count(*) FROM politica_dominio") == [(0,)]
    assert Path(tmp_path / "policies.json").read_text(encoding="utf-8") == "{broken"
