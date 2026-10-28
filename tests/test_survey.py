import json
import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from app import create_app


@pytest.fixture
def config(tmp_path):
    return {"TESTING": True, "DATA_FILE": str(tmp_path / "auth.json")}


def register(client, username="alice"):
    return client.post("/v1/auth/register", json={
        "username": username,
        "password": "throwaway",
    })


def survey_path(config):
    return Path(config["DATA_FILE"]).with_name("surveys.json")


def test_authenticated_submission_persists_only_answers_and_timestamp(config):
    client = create_app(config).test_client()
    assert register(client).status_code == 201

    response = client.post("/v1/survey", json={"answers": [1, 2, 3, 4, 5, 1, 2, 3, 4, 5]})

    assert response.status_code == 200
    assert response.get_json() == {}
    stored = json.loads(survey_path(config).read_text(encoding="utf-8"))
    assert set(stored) == {"surveys", "participants"}
    assert stored["participants"] == ["alice"]
    assert len(stored["surveys"]) == 1
    record = stored["surveys"][0]
    assert set(record) == {"answers", "submittedAt"}
    assert record["answers"] == [1, 2, 3, 4, 5, 1, 2, 3, 4, 5]
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", record["submittedAt"])
    assert "username" not in record
    serialized = json.dumps(stored["surveys"])
    assert all(value not in serialized for value in ("alice", "sereno_session", "127.0.0.1"))


def test_boundary_answers_are_accepted_for_different_accounts(config):
    first = create_app(config).test_client()
    assert register(first, "alice").status_code == 201
    assert first.post("/v1/survey", json={"answers": [1] * 10}).status_code == 200

    second = create_app(config).test_client()
    assert register(second, "bob").status_code == 201
    assert second.post("/v1/survey", json={"answers": [5] * 10}).status_code == 200

    stored = json.loads(survey_path(config).read_text(encoding="utf-8"))
    assert [item["answers"] for item in stored["surveys"]] == [[1] * 10, [5] * 10]
    assert stored["participants"] == ["alice", "bob"]


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
    path = survey_path(config)
    before = path.read_bytes()

    response = client.post("/v1/survey", data=json.dumps(payload), content_type="application/json")

    assert response.status_code == 400
    assert response.get_json() == {"error": "invalid"}
    assert path.read_bytes() == before


@pytest.mark.parametrize("body", ["", "{", "null", "[]", '"text"'])
def test_malformed_or_nonobject_json_is_invalid(config, body):
    client = create_app(config).test_client()
    assert register(client).status_code == 201
    before = survey_path(config).read_bytes()

    response = client.post("/v1/survey", data=body, content_type="application/json")

    assert response.status_code == 400
    assert response.get_json() == {"error": "invalid"}
    assert survey_path(config).read_bytes() == before


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
    assert json.loads(survey_path(config).read_text(encoding="utf-8")) == {"surveys": []}


def test_survey_storage_failure_is_controlled_and_does_not_leak_path(config):
    client = create_app(config).test_client()
    assert register(client).status_code == 201
    path = survey_path(config)
    path.write_text("corrupt /private/location", encoding="utf-8")

    response = client.post("/v1/survey", json={"answers": [3] * 10})

    assert response.status_code == 500
    assert response.get_json() == {"error": "server"}
    assert "/private/location" not in response.get_data(as_text=True)
    assert path.read_text(encoding="utf-8") == "corrupt /private/location"


def test_auth_storage_failure_is_not_bypassed(config):
    client = create_app(config).test_client()
    assert register(client).status_code == 201
    survey_before = survey_path(config).read_bytes()
    Path(config["DATA_FILE"]).write_text("corrupt auth", encoding="utf-8")

    response = client.post("/v1/survey", json={"answers": [3] * 10})

    assert response.status_code == 500
    assert response.get_json() == {"error": "server"}
    assert survey_path(config).read_bytes() == survey_before


@pytest.mark.parametrize("content", [
    "broken",
    "[]",
    "{}",
    '{"surveys": {}}',
    '{"surveys": [{"answers": [1, 2], "submittedAt": "2026-10-27T12:30:00Z"}]}',
    '{"surveys": [{"answers": [1,1,1,1,1,1,1,1,1,1], "submittedAt": "not-a-date"}]}',
    '{"surveys": [{"answers": [1,1,1,1,1,1,1,1,1,true], "submittedAt": "2026-10-27T12:30:00Z"}]}',
    '{"surveys": [{"answers": [1,1,1,1,1,1,1,1,1,1], "submittedAt": "2026-10-27T12:30:00Z", "user": "alice"}]}',
])
def test_corrupt_existing_survey_data_is_preserved(tmp_path, content):
    from app.survey.domain import SurveyStorageError
    from app.survey.json_repository import JsonSurveyRepository

    path = tmp_path / "surveys.json"
    path.write_text(content, encoding="utf-8")
    with pytest.raises(SurveyStorageError):
        JsonSurveyRepository(path)
    assert path.read_text(encoding="utf-8") == content


def test_failed_atomic_replace_keeps_original_and_cleans_temporary(tmp_path, monkeypatch):
    import app.survey.json_repository as adapter
    from app.survey.domain import SurveySubmission, SurveyStorageError
    from app.survey.json_repository import JsonSurveyRepository

    path = tmp_path / "surveys.json"
    repository = JsonSurveyRepository(path)
    before = path.read_bytes()

    def fail(*args):
        raise OSError("private path")

    monkeypatch.setattr(adapter.os, "replace", fail)
    with pytest.raises(SurveyStorageError):
        repository.submit("alice", SurveySubmission((3,) * 10, "2026-10-27T12:30:00Z"))
    assert path.read_bytes() == before
    assert list(tmp_path.iterdir()) == [path]


def test_concurrent_repository_instances_do_not_lose_submissions(tmp_path):
    from app.survey.domain import SurveySubmission
    from app.survey.json_repository import JsonSurveyRepository

    path = tmp_path / "surveys.json"

    def submit(index):
        repository = JsonSurveyRepository(path)
        repository.submit(f"user-{index:02d}", SurveySubmission(
            (index % 5 + 1,) * 10, "2026-10-27T12:30:00Z"))

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(submit, range(24)))

    submissions = JsonSurveyRepository(path).list_submissions()
    assert len(submissions) == 24
    assert sorted(item.answers[0] for item in submissions) == sorted(index % 5 + 1 for index in range(24))


def test_explicit_survey_path_and_default_path_resolution(tmp_path, monkeypatch):
    from app.config import BACKEND_ROOT, survey_data_path

    auth_path = tmp_path / "nested" / "auth.json"
    monkeypatch.chdir(tmp_path)
    assert survey_data_path(auth_path, None) == auth_path.resolve().with_name("surveys.json")
    assert survey_data_path(auth_path, "custom/survey.json") == BACKEND_ROOT / "custom/survey.json"

    explicit = tmp_path / "explicit.json"
    client = create_app({"TESTING": True, "DATA_FILE": str(auth_path),
                         "SURVEY_DATA_FILE": str(explicit)}).test_client()
    assert register(client).status_code == 201
    assert client.post("/v1/survey", json={"answers": [4] * 10}).status_code == 200
    assert explicit.exists()
    assert not auth_path.with_name("surveys.json").exists()


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
    before = survey_path(config).read_bytes()
    response = client.post(
        "/v1/survey",
        base_url="http://" + host,
        environ_overrides={"REMOTE_ADDR": peer},
        headers=headers,
        json={"answers": [3] * 10},
    )
    assert response.status_code == 403
    assert response.get_json() == {"error": "server"}
    assert survey_path(config).read_bytes() == before


def test_existing_auth_endpoints_remain_available(config):
    client = create_app(config).test_client()
    response = register(client)
    assert response.status_code == 201
    assert client.get("/v1/auth/session").get_json()["session"]["username"] == "alice"
    assert client.post("/v1/auth/logout", json={}).get_json() == {}
    assert client.post("/v1/auth/login", json={"username": "alice", "password": "throwaway"}).status_code == 200
