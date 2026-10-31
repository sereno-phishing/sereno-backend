from pathlib import Path

import pytest

from app import create_app


@pytest.fixture
def config(tmp_path):
    return {"TESTING": True, "DATA_FILE": str(tmp_path / "auth.json"),
            "HISTORY_DATA_FILE": str(tmp_path / "history.json")}


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
    assert set(entries[0]) == {"id", "domain", "status", "score", "source", "ts", "latencyMs"}
    assert entries[0]["status"] == "bloqueo" and entries[0]["source"] == "modelo"

    assert alice.delete("/v1/history").get_json() == {}
    assert alice.get("/v1/history").get_json() == {"entries": []}
    assert [entry["domain"] for entry in bob.get("/v1/history").get_json()["entries"]] == [
        "example.org"]


def test_history_storage_failure_is_server_error(config):
    client = create_app(config).test_client()
    register(client, "alice")
    Path(config["HISTORY_DATA_FILE"]).write_text("{broken", encoding="utf-8")
    for method in ("get", "delete"):
        response = getattr(client, method)("/v1/history")
        assert response.status_code == 500
        assert response.get_json() == {"error": "server"}
