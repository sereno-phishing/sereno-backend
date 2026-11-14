import pytest

from app import create_app
from app.config import settings
from conftest import rows, set_role


@pytest.fixture
def local(config):
    return {**config, "AUTH_LOCAL_HTTP": True}


def test_http_development_requires_explicit_environment_opt_in(monkeypatch):
    monkeypatch.delenv("AUTH_LOCAL_HTTP", raising=False)
    assert settings()["AUTH_LOCAL_HTTP"] is False
    monkeypatch.setenv("AUTH_LOCAL_HTTP", "1")
    assert settings()["AUTH_LOCAL_HTTP"] is True
    monkeypatch.setenv("AUTH_LOCAL_HTTP", "true")
    assert settings()["AUTH_LOCAL_HTTP"] is False


def test_cookie_policy_config_rejects_non_boolean(config):
    with pytest.raises(ValueError, match="AUTH_LOCAL_HTTP"):
        create_app({**config, "AUTH_LOCAL_HTTP": "false"})


def test_loopback_http_cookie_lifecycle_and_persistence(local):
    client = create_app(local).test_client()
    base = "http://127.0.0.1:5000"
    credentials = {"username": "local-test", "password": "throwaway"}
    response = client.post("/v1/auth/register", base_url=base, json=credentials)
    assert response.status_code == 201
    token = response.json["token"]
    cookie = response.headers["Set-Cookie"]
    assert "Secure" not in cookie
    assert all(flag in cookie for flag in ("HttpOnly", "SameSite=Lax", "Path=/"))
    assert "Domain=" not in cookie
    assert "Access-Control-Allow-Origin" not in response.headers
    recreated = create_app(local).test_client()
    recreated.set_cookie("sereno_session", token, domain="127.0.0.1")
    assert recreated.get("/v1/auth/session", base_url=base).json["session"]["token"] == token
    response = recreated.post("/v1/auth/logout", base_url=base, json={})
    assert response.status_code == 200
    assert "Max-Age=0" in response.headers["Set-Cookie"]
    assert "Secure" not in response.headers["Set-Cookie"]
    assert "SameSite=Lax" in response.headers["Set-Cookie"]
    assert recreated.get("/v1/auth/session", base_url=base).json == {"session": None}
    client.set_cookie("sereno_session", token, domain="127.0.0.1")
    assert client.get("/v1/auth/session", base_url=base).json == {"session": None}
    response = client.post("/v1/auth/login", base_url=base, json=credentials)
    assert response.status_code == 200
    users, sessions = rows(local, "usuario", "sesion")
    assert [session[0] for session in sessions] == [response.json["token"]]
    assert len(users) == 1 and "throwaway" not in repr(users)


@pytest.mark.parametrize("peer,host,headers", [
    ("192.0.2.1", "127.0.0.1:5000", {}),
    ("192.0.2.1", "127.0.0.1:5000", {"X-Forwarded-For": "127.0.0.1"}),
    ("127.0.0.1", "example.test", {}),
    ("127.0.0.1", "example.test", {"X-Forwarded-Host": "127.0.0.1"}),
    ("127.0.0.1", "127.0.0.1:5000", {"Forwarded": "for=192.0.2.1"}),
    ("127.0.0.1", "127.0.0.1:5000", {"X-Forwarded-For": "192.0.2.1"}),
    ("", "127.0.0.1:5000", {}),
])
@pytest.mark.parametrize("endpoint,method", [
    ("register", "POST"), ("login", "POST"), ("session", "GET"), ("logout", "POST"),
])
def test_development_rejects_nonloopback_or_forwarded_requests(local, peer, host, headers, endpoint, method):
    client = create_app(local).test_client()
    response = client.open("/v1/auth/" + endpoint, method=method, base_url="http://" + host,
                           environ_overrides={"REMOTE_ADDR": peer}, headers=headers,
                           json={"username": "test", "password": "throwaway"})
    assert response.status_code == 403
    assert response.json == {"error": "server"}
    assert "Set-Cookie" not in response.headers
    assert rows(local, "usuario", "sesion") == [[], []]


def test_policy_routes_accept_direct_loopback_for_an_administrator(local):
    client = create_app(local).test_client()
    base = "http://127.0.0.1:5000"
    assert client.post("/v1/auth/register", base_url=base, json={
        "username": "admin-user", "password": "throwaway",
    }).status_code == 201
    set_role(local, "admin-user", "admin")

    response = client.put("/v1/domains", base_url=base, json={"domains": []})

    assert response.status_code == 200
    assert response.get_json() == {"domains": []}


@pytest.mark.parametrize("peer,host,headers", [
    ("192.0.2.1", "127.0.0.1:5000", {}),
    ("127.0.0.1", "example.test", {}),
    ("127.0.0.1", "127.0.0.1:5000", {"Forwarded": "for=127.0.0.1"}),
    ("127.0.0.1", "127.0.0.1:5000", {"X-Forwarded-For": "127.0.0.1"}),
])
@pytest.mark.parametrize("method", ["GET", "PUT"])
def test_policy_routes_use_same_loopback_and_forwarded_guards(local, peer, host, headers, method):
    client = create_app(local).test_client()

    response = client.open(
        "/v1/domains",
        method=method,
        base_url="http://" + host,
        environ_overrides={"REMOTE_ADDR": peer},
        headers=headers,
        json={"domains": []} if method == "PUT" else None,
    )

    assert response.status_code == 403
    assert response.get_json() == {"error": "server"}
    assert rows(local, "politica_dominio") == [[]]


def test_default_cookie_policy_remains_secure(config, monkeypatch):
    monkeypatch.delenv("AUTH_LOCAL_HTTP", raising=False)
    client = create_app(config).test_client()
    response = client.post("/v1/auth/register", json={"username": "test", "password": "throwaway"})
    assert all(flag in response.headers["Set-Cookie"] for flag in ("Secure", "HttpOnly", "SameSite=None"))
    response = client.post("/v1/auth/logout", json={})
    assert all(flag in response.headers["Set-Cookie"] for flag in ("Secure", "HttpOnly", "SameSite=None"))
