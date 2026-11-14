import json
import re

import pytest

from app import create_app
from app.survey.domain import SurveyStorageError
from app.survey.postgres_repository import PostgresSurveyRepository
from conftest import rows


def stored(config):
    """-> [responses, participants] as stored."""
    return rows(config, "encuesta_respuesta", "encuesta_participante")


def register(client, username="alice"):
    return client.post("/v1/auth/register", json={
        "username": username,
        "password": "throwaway",
    })


@pytest.mark.parametrize("payload", [
    None,
    [],
    "text",
    {},
    {"answers": [1] * 9},
    {"answers": [1] * 11},
    {"answers": [0] + [1] * 9},
    {"answers": [6] + [1] * 9},
    {"answers": [True] + [1] * 9},
    {"answers": [1.0] + [1] * 9},
    {"answers": ["1"] + [1] * 9},
    {"answers": [None] + [1] * 9},
    {"answers": [1] * 10, "username": "mallory"},
])
def test_invalid_payload_is_rejected_without_survey_write(config, payload):
    client = create_app(config).test_client()
    assert register(client).status_code == 201
    before = stored(config)

    response = client.post("/v1/survey", data=json.dumps(payload), content_type="application/json")

    assert response.status_code == 400
    assert response.get_json() == {"error": "invalid"}
    assert stored(config) == before


@pytest.mark.parametrize("body", ["", "{", "null", "[]", '"text"'])
def test_malformed_or_nonobject_json_is_invalid(config, body):
    client = create_app(config).test_client()
    assert register(client).status_code == 201
    before = stored(config)

    response = client.post("/v1/survey", data=body, content_type="application/json")

    assert response.status_code == 400
    assert response.get_json() == {"error": "invalid"}
    assert stored(config) == before


def test_missing_unknown_and_revoked_sessions_are_unauthorized(config):
    client = create_app(config).test_client()
    answers = {"answers": [3] * 10}
    assert client.post("/v1/survey", json=answers).status_code == 401
    client.set_cookie("sereno_session", "unknown")
    response = client.post("/v1/survey", json=answers)
    assert response.status_code == 401
    assert response.get_json() == {"error": "invalid"}

    token = register(client).get_json()["token"]
    assert client.post("/v1/auth/logout", json={}).status_code == 200
    client.set_cookie("sereno_session", token)
    response = client.post("/v1/survey", json=answers)
    assert response.status_code == 401
    assert response.get_json() == {"error": "invalid"}
    assert stored(config) == [[], []]


def test_survey_accepts_direct_loopback_in_local_http_mode(config):
    client = create_app({**config, "AUTH_LOCAL_HTTP": True}).test_client()
    base = "http://127.0.0.1:5000"
    response = client.post("/v1/auth/register", base_url=base, json={
        "username": "alice", "password": "throwaway",
    })
    assert response.status_code == 201
    assert client.post("/v1/survey", base_url=base,
                       json={"answers": [3] * 10}).status_code == 200


@pytest.mark.parametrize("peer,host,headers", [
    ("192.0.2.1", "127.0.0.1:5000", {}),
    ("127.0.0.1", "example.test", {}),
    ("127.0.0.1", "127.0.0.1:5000", {"X-Forwarded-For": "127.0.0.1"}),
])
def test_survey_uses_same_loopback_and_forwarded_header_guard(config, peer, host, headers):
    client = create_app({**config, "AUTH_LOCAL_HTTP": True}).test_client()
    before = stored(config)
    response = client.post(
        "/v1/survey",
        base_url="http://" + host,
        environ_overrides={"REMOTE_ADDR": peer},
        headers=headers,
        json={"answers": [3] * 10},
    )
    assert response.status_code == 403
    assert response.get_json() == {"error": "server"}
    assert stored(config) == before


def test_existing_auth_endpoints_remain_available(config):
    client = create_app(config).test_client()
    response = register(client)
    assert response.status_code == 201
    assert client.get("/v1/auth/session").get_json()["session"]["username"] == "alice"
    assert client.post("/v1/auth/logout", json={}).get_json() == {}
    assert client.post("/v1/auth/login", json={"username": "alice", "password": "throwaway"}).status_code == 200


def test_authenticated_submission_persists_only_answers_and_timestamp(config):
    client = create_app(config).test_client()
    assert register(client).status_code == 201

    response = client.post("/v1/survey", json={"answers": [1, 2, 3, 4, 5, 1, 2, 3, 4, 5]})

    assert response.status_code == 200
    assert response.get_json() == {}
    responses, participants = stored(config)
    assert len(participants) == 1
    (_, answers, sent), = responses
    assert answers == [1, 2, 3, 4, 5, 1, 2, 3, 4, 5]
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", sent.strftime("%Y-%m-%dT%H:%M:%SZ"))
    assert all(value not in repr(responses) for value in ("alice", "sereno_session", "127.0.0.1"))


def test_boundary_answers_are_accepted_for_different_accounts(config):
    first = create_app(config).test_client()
    assert register(first, "alice").status_code == 201
    assert first.post("/v1/survey", json={"answers": [1] * 10}).status_code == 200

    second = create_app(config).test_client()
    assert register(second, "bob").status_code == 201
    assert second.post("/v1/survey", json={"answers": [5] * 10}).status_code == 200

    responses, participants = stored(config)
    assert [response[1] for response in responses] == [[1] * 10, [5] * 10]
    assert len(participants) == 2


def test_survey_storage_failure_is_controlled_and_does_not_leak_details(config, monkeypatch):
    def fail(*arguments):
        raise SurveyStorageError("Storage unavailable: /private/location")

    client = create_app(config).test_client()
    assert register(client).status_code == 201
    monkeypatch.setattr(PostgresSurveyRepository, "submit", fail)

    response = client.post("/v1/survey", json={"answers": [3] * 10})

    assert response.status_code == 500
    assert response.get_json() == {"error": "server"}
    assert "/private/location" not in response.get_data(as_text=True)
    assert stored(config) == [[], []]


def test_auth_storage_failure_is_not_bypassed(config, database_down, monkeypatch):
    client = create_app(config).test_client()
    assert register(client).status_code == 201
    database_down()

    response = client.post("/v1/survey", json={"answers": [3] * 10})

    assert response.status_code == 500
    assert response.get_json() == {"error": "server"}
    monkeypatch.undo()
    assert stored(config) == [[], []]
