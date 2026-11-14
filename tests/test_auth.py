import json
import re
from concurrent.futures import ThreadPoolExecutor

import pytest

from app import create_app
from app.auth.domain import Credentials
from app.auth.postgres_repository import PostgresAuthRepository
from app.auth.service import AuthService
from conftest import rows, set_role


@pytest.fixture
def client(config):
    return create_app(config).test_client()


def register(client, username="alice", password="secret", **extra):
    return client.post("/v1/auth/register", json={
        "username": username, "password": password, **extra,
    })


@pytest.mark.parametrize("requested_role", ["admin", "administrador", "unknown"])
def test_register_exact_public_contract(client, config, requested_role):
    response = register(client, username=" alice ", role=requested_role)
    assert response.status_code == 201
    body = response.get_json()
    assert set(body) == {"user", "token"}
    assert set(body["user"]) == {"username", "isAdmin", "createdAt"}
    assert body["user"]["username"] == "alice"
    assert body["user"]["isAdmin"] is False
    assert "role" not in body["user"]
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}", body["user"]["createdAt"])
    assert len(body["token"]) >= 32
    cookie = response.headers["Set-Cookie"]
    assert "sereno_session=" in cookie
    assert all(flag in cookie for flag in ("Secure", "HttpOnly", "SameSite=None"))
    (stored,), = rows(config, "usuario")
    assert stored[1] == "alice" and "secret" not in stored[2]
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


def test_smoke_persisted_session_and_revocation(config):
    app = create_app(config)
    client = app.test_client()
    assert client.get("/v1/auth/session").get_json() == {"session": None}
    token = register(client).get_json()["token"]
    recreated = create_app(config).test_client()
    recreated.set_cookie("sereno_session", token)
    response = recreated.get("/v1/auth/session")
    assert response.status_code == 200
    session = response.get_json()["session"]
    assert set(session) == {"username", "isAdmin", "loginAt", "token"}
    assert session["username"] == "alice"
    assert session["isAdmin"] is False
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


def test_repository_contract_and_injected_hashing(database):
    repo = PostgresAuthRepository(database)
    service = AuthService(repo, lambda value: "hashed:" + value,
                          lambda hashed, value: hashed == "hashed:" + value,
                          clock=lambda: "2026-10-27T12:30", token_factory=lambda: "token")
    user, token = service.register(Credentials("alice", "secret"))
    assert user == {"username": "alice", "isAdmin": False, "createdAt": "2026-10-27T12:30"}
    assert repo.get_user("alice").password_hash == "hashed:secret"
    assert repo.get_session(token).username == "alice"
    assert service.login(Credentials("alice", "secret"))[0] == user
    repo.delete_session(token)
    assert repo.get_session(token) is None
    assert repo.get_user("missing") is None


@pytest.mark.parametrize("body", ["{", "[]", "null", '"text"'])
def test_logout_rejects_invalid_json(client, body):
    response = client.post("/v1/auth/logout", data=body, content_type="application/json")
    assert response.status_code == 400
    assert response.get_json() == {"error": "invalid"}


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


def test_arbitrary_verifier_errors_are_not_hidden(database):
    def broken_verifier(hashed, password):
        raise RuntimeError("unexpected verifier failure")

    service = AuthService(PostgresAuthRepository(database),
                          lambda value: "hash", broken_verifier)
    service.register(Credentials("alice", "secret"))
    with pytest.raises(RuntimeError, match="unexpected verifier failure"):
        service.login(Credentials("alice", "secret"))


def test_storage_failure_is_controlled(client, database_down):
    token = register(client).get_json()["token"]
    database_down()
    for method, url, kwargs in (
        (client.post, "/v1/auth/register", {"json": {"username": "a", "password": "p"}}),
        (client.post, "/v1/auth/login", {"json": {"username": "alice", "password": "secret"}}),
        (client.get, "/v1/auth/session", {}),
        (client.post, "/v1/auth/logout", {"json": {}}),
    ):
        client.set_cookie("sereno_session", token)
        response = method(url, **kwargs)
        assert response.status_code == 500
        assert response.get_json() == {"error": "server"}
        assert "refused" not in response.get_data(as_text=True)


def test_nothing_is_lost_while_the_database_is_unreachable(client, config, database_down,
                                                           monkeypatch):
    token = register(client).get_json()["token"]
    before = rows(config, "usuario", "sesion")
    database_down()
    assert client.post("/v1/auth/logout", json={}).status_code == 500
    monkeypatch.undo()
    assert rows(config, "usuario", "sesion") == before
    client.set_cookie("sereno_session", token)
    assert client.get("/v1/auth/session").get_json()["session"]["username"] == "alice"


def test_concurrent_duplicate_registration_is_atomic(config):
    def attempt(_):
        return register(create_app(config).test_client()).status_code

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(attempt, range(4)))
    assert sorted(results) == [201, 409, 409, 409]
    users, sessions = rows(config, "usuario", "sesion")
    assert len(users) == len(sessions) == 1


def test_stored_administrator_role_is_returned_by_existing_session(client, config):
    token = register(client).get_json()["token"]
    set_role(config, "alice", "admin")

    client.set_cookie("sereno_session", token)
    response = client.get("/v1/auth/session")

    assert response.status_code == 200
    assert response.get_json()["session"]["isAdmin"] is True


def test_corrupt_password_hash_is_server_error_and_preserved(client, config, database):
    assert register(client).status_code == 201
    with database.transaction(RuntimeError) as connection:
        connection.execute("UPDATE usuario SET hash_contrasena = 'scrypt:bad$abc$def'")
    before = rows(config, "usuario", "sesion")
    response = client.post("/v1/auth/login", json={"username": "alice", "password": "secret"})
    assert response.status_code == 500
    assert response.get_json() == {"error": "server"}
    assert rows(config, "usuario", "sesion") == before
