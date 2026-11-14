from concurrent.futures import ThreadPoolExecutor

import pytest

from app import create_app
from app.survey.domain import SurveyStorageError
from app.survey.postgres_repository import PostgresSurveyRepository
from conftest import rows


def stored(config):
    """-> [responses, participants] as stored."""
    return rows(config, "encuesta_respuesta", "encuesta_participante")


ANSWERS = {"answers": [3] * 10}


def register(client, username):
    return client.post("/v1/auth/register", json={
        "username": username,
        "password": "throwaway",
    })


def login(client, username):
    return client.post("/v1/auth/login", json={
        "username": username,
        "password": "throwaway",
    })


def test_status_and_duplicate_survive_logout_login_app_recreation(config):
    client = create_app(config).test_client()
    assert register(client, "alice").status_code == 201
    assert client.get("/v1/survey").get_json() == {"submitted": False}
    assert client.post("/v1/survey", json=ANSWERS).status_code == 200
    assert client.get("/v1/survey").get_json() == {"submitted": True}
    before = stored(config)

    duplicate = client.post("/v1/survey", json=ANSWERS)
    assert duplicate.status_code == 409
    assert duplicate.get_json() == {"error": "already_submitted"}
    assert stored(config) == before

    assert client.post("/v1/auth/logout", json={}).status_code == 200
    recreated = create_app(config).test_client()
    assert login(recreated, "alice").status_code == 200
    assert recreated.get("/v1/survey").get_json() == {"submitted": True}
    duplicate = recreated.post("/v1/survey", json=ANSWERS)
    assert duplicate.status_code == 409
    assert duplicate.get_json() == {"error": "already_submitted"}
    assert stored(config) == before


def test_other_account_is_eligible_and_auth_username_is_case_sensitive(config):
    alice = create_app(config).test_client()
    assert register(alice, "Alice").status_code == 201
    assert alice.post("/v1/survey", json={"answers": [1] * 10}).status_code == 200

    bob = create_app(config).test_client()
    assert register(bob, "alice").status_code == 201
    assert bob.get("/v1/survey").get_json() == {"submitted": False}
    assert bob.post("/v1/survey", json={"answers": [5] * 10}).status_code == 200
    assert bob.get("/v1/survey").get_json() == {"submitted": True}

    responses, participants = stored(config)
    assert len(participants) == 2
    assert [response[1] for response in responses] == [[1] * 10, [5] * 10]


def test_spoofed_username_is_rejected_without_affecting_real_account(config):
    client = create_app(config).test_client()
    assert register(client, "alice").status_code == 201
    before = stored(config)

    response = client.post("/v1/survey", json={"answers": [3] * 10, "username": "bob"})

    assert response.status_code == 400
    assert response.get_json() == {"error": "invalid"}
    assert stored(config) == before
    assert client.get("/v1/survey").get_json() == {"submitted": False}


def test_invalid_answers_do_not_consume_eligibility(config):
    client = create_app(config).test_client()
    assert register(client, "alice").status_code == 201
    before = stored(config)

    response = client.post("/v1/survey", json={"answers": [0] + [3] * 9})

    assert response.status_code == 400
    assert stored(config) == before
    assert client.get("/v1/survey").get_json() == {"submitted": False}
    assert client.post("/v1/survey", json=ANSWERS).status_code == 200


def test_concurrent_same_account_accepts_exactly_one(config):
    owner = create_app(config).test_client()
    assert register(owner, "alice").status_code == 201
    tokens = [login(create_app(config).test_client(), "alice").get_json()["token"] for _ in range(12)]

    def submit(token):
        client = create_app(config).test_client()
        client.set_cookie("sereno_session", token)
        response = client.post("/v1/survey", json=ANSWERS)
        return response.status_code, response.get_json()

    with ThreadPoolExecutor(max_workers=12) as pool:
        results = list(pool.map(submit, tokens))

    assert [status for status, _ in results].count(200) == 1
    assert [status for status, _ in results].count(409) == 11
    assert all(body == ({} if status == 200 else {"error": "already_submitted"})
               for status, body in results)
    responses, participants = stored(config)
    assert len(participants) == len(responses) == 1


def test_concurrent_different_accounts_do_not_lose_entries(config):
    usernames = [f"user-{index:02d}" for index in range(16)]
    tokens = []
    for username in usernames:
        client = create_app(config).test_client()
        tokens.append(register(client, username).get_json()["token"])

    def submit(item):
        username, token = item
        client = create_app(config).test_client()
        client.set_cookie("sereno_session", token)
        return username, client.post("/v1/survey", json=ANSWERS).status_code

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(submit, zip(usernames, tokens)))

    assert all(status == 200 for _, status in results)
    responses, participants = stored(config)
    assert len(participants) == len(responses) == len(usernames)


def test_status_requires_valid_session_and_reports_storage_errors(config, database_down):
    client = create_app(config).test_client()
    response = client.get("/v1/survey")
    assert response.status_code == 401
    assert response.get_json() == {"error": "invalid"}

    client.set_cookie("sereno_session", "unknown")
    response = client.get("/v1/survey")
    assert response.status_code == 401
    assert response.get_json() == {"error": "invalid"}

    token = register(client, "alice").get_json()["token"]
    assert client.post("/v1/auth/logout", json={}).status_code == 200
    client.set_cookie("sereno_session", token)
    response = client.get("/v1/survey")
    assert response.status_code == 401
    assert response.get_json() == {"error": "invalid"}

    assert login(client, "alice").status_code == 200
    database_down()
    response = client.get("/v1/survey")
    assert response.status_code == 500
    assert response.get_json() == {"error": "server"}


def test_status_uses_same_local_http_guard(config):
    client = create_app({**config, "AUTH_LOCAL_HTTP": True}).test_client()
    response = client.get(
        "/v1/survey",
        base_url="http://example.test",
        environ_overrides={"REMOTE_ADDR": "127.0.0.1"},
    )
    assert response.status_code == 403
    assert response.get_json() == {"error": "server"}


def test_a_failed_submission_does_not_consume_eligibility(config, database):
    """The participation mark and the response are one transaction: when the
    response cannot be stored, the mark is rolled back with it."""
    client = create_app(config).test_client()
    assert register(client, "alice").status_code == 201
    with database.transaction(RuntimeError) as connection:
        connection.execute("ALTER TABLE encuesta_respuesta RENAME TO respuesta_apartada")
    try:
        response = client.post("/v1/survey", json=ANSWERS)
    finally:
        with database.transaction(RuntimeError) as connection:
            connection.execute("ALTER TABLE respuesta_apartada RENAME TO encuesta_respuesta")

    assert response.status_code == 500
    assert response.get_json() == {"error": "server"}
    assert stored(config) == [[], []]
    assert client.get("/v1/survey").get_json() == {"submitted": False}
    assert client.post("/v1/survey", json=ANSWERS).status_code == 200


def test_status_reports_survey_storage_error_without_leaking_data(config, monkeypatch):
    def fail(*arguments):
        raise SurveyStorageError("Storage unavailable: /private/location")

    client = create_app(config).test_client()
    assert register(client, "alice").status_code == 201
    monkeypatch.setattr(PostgresSurveyRepository, "has_submitted", fail)

    response = client.get("/v1/survey")

    assert response.status_code == 500
    assert response.get_json() == {"error": "server"}
    assert "/private/location" not in response.get_data(as_text=True)
