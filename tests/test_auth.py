import json
import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from app import create_app
from app.auth.domain import Credentials, StorageError
from app.auth.json_repository import JsonAuthRepository
from app.auth.service import AuthService


@pytest.fixture
def settings(tmp_path):
    return {"TESTING": True, "DATA_FILE": str(tmp_path / "db.json")}


@pytest.fixture
def client(settings):
    return create_app(settings).test_client()


def register(client, username="alice", password="secret", **extra):
    return client.post("/v1/auth/register", json={
        "username": username, "password": password, **extra,
    })


@pytest.mark.parametrize("requested_role", ["admin", "administrador", "unknown"])
def test_register_exact_public_contract(client, settings, requested_role):
    response = register(client, username=" alice ", role=requested_role)
    assert response.status_code == 201
    body = response.get_json()
    assert set(body) == {"user", "token"}
    assert set(body["user"]) == {"username", "role", "createdAt"}
    assert body["user"]["username"] == "alice"
    assert body["user"]["role"] == "usuario"
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}", body["user"]["createdAt"])
    assert len(body["token"]) >= 32
    cookie = response.headers["Set-Cookie"]
    assert "sereno_session=" in cookie
    assert all(flag in cookie for flag in ("Secure", "HttpOnly", "SameSite=None"))
    stored = json.loads(open(settings["DATA_FILE"], encoding="utf-8").read())
    assert stored["users"]["alice"]["password_hash"] != "secret"
    assert "password" not in stored["users"]["alice"]
    assert "password" not in response.get_data(as_text=True)


@pytest.mark.parametrize("endpoint,status", [("register", 400), ("login", 401)])
@pytest.mark.parametrize("payload", [
    None, [], "text", {}, {"username": "alice"},
    {"username": 5, "password": "x"},
    {"username": "alice", "password": 5},
    {"username": "  ", "password": "x"},
    {"username": "alice", "password": ""},
    {"username": None, "password": "x"},
    {"username": "alice", "password": None},
])
def test_invalid_credentials(client, endpoint, status, payload):
    response = client.post(f"/v1/auth/{endpoint}", data=json.dumps(payload),
                           content_type="application/json")
    assert response.status_code == status
    assert response.get_json() == {"error": "invalid"}


@pytest.mark.parametrize("endpoint,status", [("register", 400), ("login", 401)])
def test_malformed_or_missing_json(client, endpoint, status):
    for data in ("{", ""):
        response = client.post(f"/v1/auth/{endpoint}", data=data,
                               content_type="application/json")
        assert response.status_code == status
        assert response.get_json() == {"error": "invalid"}


def test_duplicates_case_sensitive_login_and_bad_password(client):
    assert register(client).status_code == 201
    response = register(client)
    assert response.status_code == 409
    assert response.get_json() == {"error": "duplicate"}
    assert register(client, username="Alice").status_code == 201
    response = client.post("/v1/auth/login", json={"username": "alice", "password": "bad"})
    assert response.status_code == 401
    assert response.get_json() == {"error": "invalid"}
    response = client.post("/v1/auth/login", json={"username": "alice", "password": "secret"})
    assert response.status_code == 200
    assert set(response.get_json()) == {"user", "token"}


def test_smoke_persisted_session_and_revocation(settings):
    app = create_app(settings)
    client = app.test_client()
    assert client.get("/v1/auth/session").get_json() == {"session": None}
    token = register(client).get_json()["token"]
    recreated = create_app(settings).test_client()
    recreated.set_cookie("sereno_session", token)
    response = recreated.get("/v1/auth/session")
    assert response.status_code == 200
    session = response.get_json()["session"]
    assert set(session) == {"username", "role", "loginAt", "token"}
    assert session["username"] == "alice"
    assert session["role"] == "usuario"
    assert session["token"] == token
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}", session["loginAt"])
    response = recreated.post("/v1/auth/logout", json={})
    assert response.status_code == 200
    assert response.get_json() == {}
    assert "Max-Age=0" in response.headers["Set-Cookie"]
    client.set_cookie("sereno_session", token)
    assert client.get("/v1/auth/session").get_json() == {"session": None}
    assert client.post("/v1/auth/logout", json={}).get_json() == {}
    client.set_cookie("sereno_session", "unknown")
    assert client.get("/v1/auth/session").get_json() == {"session": None}


def test_unknown_driver_fails_closed(settings):
    with pytest.raises(ValueError, match="Unsupported storage driver"):
        create_app({**settings, "STORAGE_DRIVER": "postgres"})


@pytest.mark.parametrize("content", ["broken", "[]", '{}', '{"users": {}, "sessions": []}',
    '{"users": {"alice": {}}, "sessions": {}}',
    '{"users": {}, "sessions": {"orphan": {"username": "x", "loginAt": "2026-01-01T00:00", "token": "orphan"}}}',
])
def test_corrupt_data_preserved(tmp_path, content):
    path = tmp_path / "db.json"
    path.write_text(content, encoding="utf-8")
    with pytest.raises(StorageError):
        JsonAuthRepository(path)
    assert path.read_text(encoding="utf-8") == content


def test_storage_failure_is_controlled(client, settings):
    from pathlib import Path

    path = Path(settings["DATA_FILE"])
    path.write_text("corrupt", encoding="utf-8")
    for method, url, kwargs in (
        (client.post, "/v1/auth/register", {"json": {"username": "a", "password": "p"}}),
        (client.post, "/v1/auth/login", {"json": {"username": "a", "password": "p"}}),
        (client.get, "/v1/auth/session", {}),
        (client.post, "/v1/auth/logout", {"json": {}}),
    ):
        client.set_cookie("sereno_session", "token")
        response = method(url, **kwargs)
        assert response.status_code == 500
        assert response.get_json() == {"error": "server"}
    assert path.read_text(encoding="utf-8") == "corrupt"


def test_repository_contract_and_injected_hashing(tmp_path):
    repo = JsonAuthRepository(tmp_path / "db.json")
    service = AuthService(repo, lambda value: "hashed:" + value,
                          lambda hashed, value: hashed == "hashed:" + value,
                          clock=lambda: "2026-10-27T12:30", token_factory=lambda: "token")
    user, token = service.register(Credentials("alice", "secret"))
    assert user == {"username": "alice", "role": "usuario", "createdAt": "2026-10-27T12:30"}
    assert repo.get_user("alice").password_hash == "hashed:secret"
    assert repo.get_session(token).username == "alice"
    assert service.login(Credentials("alice", "secret"))[0] == user
    repo.delete_session(token)
    assert repo.get_session(token) is None
    assert repo.get_user("missing") is None


def test_concurrent_duplicate_registration_is_atomic(tmp_path):
    path = tmp_path / "db.json"

    def attempt(_):
        client = create_app({"TESTING": True, "DATA_FILE": str(path)}).test_client()
        return register(client).status_code

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(attempt, range(4)))
    assert sorted(results) == [201, 409, 409, 409]
    data = json.loads(path.read_text(encoding="utf-8"))
    assert len(data["users"]) == len(data["sessions"]) == 1


def test_failed_atomic_replace_keeps_original(tmp_path, monkeypatch):
    import app.auth.json_repository as adapter

    path = tmp_path / "db.json"
    repo = JsonAuthRepository(path)
    before = path.read_bytes()

    def fail(*args):
        raise OSError("private path")

    monkeypatch.setattr(adapter.os, "replace", fail)
    service = AuthService(repo, lambda value: "hash", lambda hashed, value: True)
    with pytest.raises(StorageError):
        service.register(Credentials("alice", "secret"))
    assert path.read_bytes() == before
    assert list(tmp_path.iterdir()) == [path]


def test_no_placeholder_endpoints(client):
    assert client.get("/v1/history").status_code == 404


@pytest.mark.parametrize("body", ["{", "[]", "null", '"text"'])
def test_logout_rejects_invalid_json(client, body):
    response = client.post("/v1/auth/logout", data=body, content_type="application/json")
    assert response.status_code == 400
    assert response.get_json() == {"error": "invalid"}


def test_relative_storage_path_ignores_invocation_directory(tmp_path, monkeypatch):
    from app.config import BACKEND_ROOT, data_path

    monkeypatch.chdir(tmp_path)
    assert data_path("data/db.json") == BACKEND_ROOT / "data/db.json"
    assert data_path(tmp_path / "db.json") == tmp_path / "db.json"


def test_whitespace_password_is_not_trimmed(client):
    assert register(client, password=" ").status_code == 201
    response = client.post("/v1/auth/login", json={"username": "alice", "password": " "})
    assert response.status_code == 200


@pytest.mark.parametrize("endpoint,status", [("register", 400), ("login", 401)])
@pytest.mark.parametrize("field", ["username", "password"])
def test_unencodable_credentials_are_invalid(client, endpoint, status, field):
    assert register(client).status_code == 201
    credentials = {"username": "alice", "password": "secret", field: "\ud800"}
    response = client.post(f"/v1/auth/{endpoint}", json=credentials)
    assert response.status_code == status
    assert response.get_json() == {"error": "invalid"}


def test_persisted_administrator_role_is_valid_and_returned_by_existing_session(client, settings):
    token = register(client).get_json()["token"]
    path = Path(settings["DATA_FILE"])
    data = json.loads(path.read_text(encoding="utf-8"))
    data["users"]["alice"]["role"] = "administrador"
    path.write_text(json.dumps(data), encoding="utf-8")

    client.set_cookie("sereno_session", token)
    response = client.get("/v1/auth/session")

    assert response.status_code == 200
    assert response.get_json()["session"]["role"] == "administrador"


@pytest.mark.parametrize("role", ["admin", "unknown", "", None, 3])
def test_unknown_persisted_roles_fail_closed_and_preserve_bytes(settings, role):
    client = create_app(settings).test_client()
    assert register(client).status_code == 201
    path = Path(settings["DATA_FILE"])
    data = json.loads(path.read_text(encoding="utf-8"))
    data["users"]["alice"]["role"] = role
    path.write_text(json.dumps(data), encoding="utf-8")
    before = path.read_bytes()

    response = client.get("/v1/auth/session")

    assert response.status_code == 500
    assert response.get_json() == {"error": "server"}
    assert path.read_bytes() == before


def test_corrupt_password_hash_is_server_error_and_preserved(client, settings):
    from pathlib import Path

    assert register(client).status_code == 201
    path = Path(settings["DATA_FILE"])
    data = json.loads(path.read_text(encoding="utf-8"))
    data["users"]["alice"]["password_hash"] = "scrypt:bad$abc$def"
    path.write_text(json.dumps(data), encoding="utf-8")
    before = path.read_bytes()
    response = client.post("/v1/auth/login", json={"username": "alice", "password": "secret"})
    assert response.status_code == 500
    assert response.get_json() == {"error": "server"}
    assert path.read_bytes() == before


def test_arbitrary_verifier_errors_are_not_hidden(tmp_path):
    def broken_verifier(hashed, password):
        raise RuntimeError("unexpected verifier failure")

    service = AuthService(JsonAuthRepository(tmp_path / "db.json"),
                          lambda value: "hash", broken_verifier)
    service.register(Credentials("alice", "secret"))
    with pytest.raises(RuntimeError, match="unexpected verifier failure"):
        service.login(Credentials("alice", "secret"))
