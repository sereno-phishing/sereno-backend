import pytest

from app import create_app
from app.history.domain import HistoryStorageError
from app.history.postgres_repository import PostgresHistoryRepository


def register(client, username):
    response = client.post("/v1/auth/register",
                           json={"username": username, "password": "throwaway"})
    assert response.status_code == 201


@pytest.mark.parametrize("method", ["get", "delete"])
def test_history_requires_a_session(config, method):
    response = getattr(create_app(config).test_client(), method)("/v1/history")
    assert response.status_code == 401
    assert response.get_json() == {"error": "invalid"}


def test_history_lists_own_entries_newest_first_and_clears(config):
    alice = create_app(config).test_client()
    bob = create_app(config).test_client()
    register(alice, "alice")
    register(bob, "bob")
    alice.post("/v1/verdict", json={"url": "https://tienda-servicios.pe/"})
    alice.post("/v1/verdict", json={"url": "https://pago-servicios-linea.net/"})
    bob.post("/v1/verdict", json={"url": "https://example.org/"})

    entries = alice.get("/v1/history").get_json()["entries"]
    assert [entry["domain"] for entry in entries] == ["pago-servicios-linea.net",
                                                      "tienda-servicios.pe"]
    assert set(entries[0]) == {"id", "domain", "verdict", "policy", "score", "source", "ts",
                               "latencyMs"}
    assert (entries[0]["verdict"], entries[0]["policy"]) == ("phishing", "advertencia")
    assert entries[0]["source"] == "modelo" and entries[1]["verdict"] == "legitima"

    assert alice.delete("/v1/history").get_json() == {}
    assert alice.get("/v1/history").get_json() == {"entries": []}
    assert [entry["domain"] for entry in bob.get("/v1/history").get_json()["entries"]] == [
        "example.org"]




@pytest.mark.parametrize("method, operation", [("get", "list"), ("delete", "clear")])
def test_history_storage_failure_is_server_error(config, monkeypatch, method, operation):
    def fail(*arguments):
        raise HistoryStorageError("Storage unavailable")

    client = create_app(config).test_client()
    register(client, "alice")
    monkeypatch.setattr(PostgresHistoryRepository, operation, fail)
    response = getattr(client, method)("/v1/history")
    assert response.status_code == 500
    assert response.get_json() == {"error": "server"}


@pytest.mark.parametrize("method", ["get", "delete"])
def test_history_reports_an_unreachable_database_as_server_error(config, database_down, method):
    client = create_app(config).test_client()
    register(client, "alice")
    database_down()
    response = getattr(client, method)("/v1/history")
    assert response.status_code == 500
    assert response.get_json() == {"error": "server"}
