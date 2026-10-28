import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from app import create_app


ANSWERS = {"answers": [3] * 10}


@pytest.fixture
def config(tmp_path):
    return {"TESTING": True, "DATA_FILE": str(tmp_path / "auth.json")}


def survey_path(config):
    return Path(config["DATA_FILE"]).with_name("surveys.json")


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


def test_repository_contract_atomically_records_participation_and_rejects_duplicate(tmp_path):
    from app.survey.domain import AlreadySubmitted, SurveySubmission
    from app.survey.json_repository import JsonSurveyRepository

    path = tmp_path / "surveys.json"
    first = JsonSurveyRepository(path)
    second = JsonSurveyRepository(path)
    submission = SurveySubmission((3,) * 10, "2026-10-27T12:30:00Z")

    assert first.has_submitted("alice") is False
    first.submit("alice", submission)
    assert second.has_submitted("alice") is True
    before = path.read_bytes()

    with pytest.raises(AlreadySubmitted):
        second.submit("alice", submission)

    assert path.read_bytes() == before
    assert len(first.list_submissions()) == 1


def test_status_and_duplicate_survive_logout_login_app_recreation(config):
    client = create_app(config).test_client()
    assert register(client, "alice").status_code == 201
    assert client.get("/v1/survey").get_json() == {"submitted": False}
    assert client.post("/v1/survey", json=ANSWERS).status_code == 200
    assert client.get("/v1/survey").get_json() == {"submitted": True}
    before = survey_path(config).read_bytes()

    duplicate = client.post("/v1/survey", json=ANSWERS)
    assert duplicate.status_code == 409
    assert duplicate.get_json() == {"error": "already_submitted"}
    assert survey_path(config).read_bytes() == before

    assert client.post("/v1/auth/logout", json={}).status_code == 200
    recreated = create_app(config).test_client()
    assert login(recreated, "alice").status_code == 200
    assert recreated.get("/v1/survey").get_json() == {"submitted": True}
    duplicate = recreated.post("/v1/survey", json=ANSWERS)
    assert duplicate.status_code == 409
    assert duplicate.get_json() == {"error": "already_submitted"}
    assert survey_path(config).read_bytes() == before


def test_other_account_is_eligible_and_auth_username_is_case_sensitive(config):
    alice = create_app(config).test_client()
    assert register(alice, "Alice").status_code == 201
    assert alice.post("/v1/survey", json={"answers": [1] * 10}).status_code == 200

    bob = create_app(config).test_client()
    assert register(bob, "alice").status_code == 201
    assert bob.get("/v1/survey").get_json() == {"submitted": False}
    assert bob.post("/v1/survey", json={"answers": [5] * 10}).status_code == 200
    assert bob.get("/v1/survey").get_json() == {"submitted": True}

    stored = json.loads(survey_path(config).read_text(encoding="utf-8"))
    assert stored["participants"] == ["Alice", "alice"]
    assert [record["answers"] for record in stored["surveys"]] == [[1] * 10, [5] * 10]
    assert all("username" not in record for record in stored["surveys"])


def test_spoofed_username_is_rejected_without_affecting_real_account(config):
    client = create_app(config).test_client()
    assert register(client, "alice").status_code == 201
    before = survey_path(config).read_bytes()

    response = client.post("/v1/survey", json={"answers": [3] * 10, "username": "bob"})

    assert response.status_code == 400
    assert response.get_json() == {"error": "invalid"}
    assert survey_path(config).read_bytes() == before
    assert client.get("/v1/survey").get_json() == {"submitted": False}


def test_invalid_answers_do_not_consume_eligibility(config):
    client = create_app(config).test_client()
    assert register(client, "alice").status_code == 201
    before = survey_path(config).read_bytes()

    response = client.post("/v1/survey", json={"answers": [0] + [3] * 9})

    assert response.status_code == 400
    assert survey_path(config).read_bytes() == before
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
    stored = json.loads(survey_path(config).read_text(encoding="utf-8"))
    assert stored["participants"] == ["alice"]
    assert len(stored["surveys"]) == 1


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
    stored = json.loads(survey_path(config).read_text(encoding="utf-8"))
    assert stored["participants"] == sorted(usernames)
    assert len(stored["surveys"]) == len(usernames)


def test_failed_atomic_replace_does_not_consume_eligibility(config, monkeypatch):
    import app.survey.json_repository as adapter

    client = create_app(config).test_client()
    assert register(client, "alice").status_code == 201
    path = survey_path(config)
    before = path.read_bytes()

    def fail(*args):
        raise OSError("replace failed")

    monkeypatch.setattr(adapter.os, "replace", fail)
    response = client.post("/v1/survey", json=ANSWERS)

    assert response.status_code == 500
    assert response.get_json() == {"error": "server"}
    assert path.read_bytes() == before
    assert client.get("/v1/survey").get_json() == {"submitted": False}


def test_legacy_anonymous_records_are_preserved_and_upgraded_only_on_success(config):
    path = survey_path(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    legacy = {"surveys": [{
        "answers": [2] * 10,
        "submittedAt": "2026-10-27T12:30:00Z",
    }]}
    original = json.dumps(legacy, indent=2).encode()
    path.write_bytes(original)

    client = create_app(config).test_client()
    assert path.read_bytes() == original
    assert register(client, "alice").status_code == 201
    assert client.get("/v1/survey").get_json() == {"submitted": False}
    assert path.read_bytes() == original
    assert client.post("/v1/survey", json=ANSWERS).status_code == 200

    stored = json.loads(path.read_text(encoding="utf-8"))
    assert stored["surveys"][0] == legacy["surveys"][0]
    assert stored["participants"] == ["alice"]
    assert len(stored["surveys"]) == 2


@pytest.mark.parametrize("participants", [
    None,
    {},
    "alice",
    [1],
    [""],
    ["alice", "alice"],
    ["bob", "alice"],
])
def test_malformed_participants_fail_closed_and_preserve_bytes(tmp_path, participants):
    from app.survey.domain import SurveyStorageError
    from app.survey.json_repository import JsonSurveyRepository

    path = tmp_path / "surveys.json"
    content = json.dumps({"surveys": [], "participants": participants})
    path.write_text(content, encoding="utf-8")

    with pytest.raises(SurveyStorageError):
        JsonSurveyRepository(path)

    assert path.read_text(encoding="utf-8") == content


def test_status_requires_valid_session_and_reports_storage_errors(config):
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
    Path(config["DATA_FILE"]).write_text("corrupt auth", encoding="utf-8")
    response = client.get("/v1/survey")
    assert response.status_code == 500
    assert response.get_json() == {"error": "server"}


def test_status_reports_survey_storage_error_without_leaking_data(config):
    client = create_app(config).test_client()
    assert register(client, "alice").status_code == 201
    path = survey_path(config)
    path.write_text("corrupt /private/location", encoding="utf-8")

    response = client.get("/v1/survey")

    assert response.status_code == 500
    assert response.get_json() == {"error": "server"}
    assert "/private/location" not in response.get_data(as_text=True)
    assert path.read_text(encoding="utf-8") == "corrupt /private/location"


def test_status_uses_same_local_http_guard(config):
    client = create_app({**config, "AUTH_LOCAL_HTTP": True}).test_client()
    response = client.get(
        "/v1/survey",
        base_url="http://example.test",
        environ_overrides={"REMOTE_ADDR": "127.0.0.1"},
    )
    assert response.status_code == 403
    assert response.get_json() == {"error": "server"}
