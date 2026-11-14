"""The development-only user administration (DEV_TOOLS): absent unless
enabled, loopback only, and closed to cross-site requests."""
import json
import re
from pathlib import Path

import pytest

from app import create_app
from app.config import settings


BASE = "http://127.0.0.1:5000"
DEV = {"X-Sereno-Dev": "1"}
ROUTES = [("get", "/dev/users"), ("get", "/dev/api/users"),
          ("put", "/dev/api/users/alice/role"), ("delete", "/dev/api/users/alice")]


@pytest.fixture
def config(database, redis_url):
    """The tool runs on PostgreSQL only (the JSON storage is being retired)."""
    return {"TESTING": True, "AUTH_LOCAL_HTTP": True, "DEV_TOOLS": True,
            "STORAGE_DRIVER": "postgres", "DATABASE_URL": database.url, "REDIS_URL": redis_url}


@pytest.fixture
def json_config(tmp_path):
    return {"TESTING": True, "AUTH_LOCAL_HTTP": True, "DATA_FILE": str(tmp_path / "db.json")}


class Client:
    """Flask's test client pinned to the loopback origin the tool requires."""

    def __init__(self, config):
        self.client = create_app(config).test_client()

    def __getattr__(self, method):
        def call(path, **keywords):
            keywords.setdefault("base_url", BASE)
            return getattr(self.client, method)(path, **keywords)
        return call


def register(client, username):
    response = client.post("/v1/auth/register",
                           json={"username": username, "password": "throwaway"})
    assert response.status_code == 201
    return response.get_json()["token"]


def set_role(client, username, role, **keywords):
    keywords.setdefault("headers", DEV)
    return client.put(f"/dev/api/users/{username}/role", json={"role": role}, **keywords)


def test_dev_tools_require_explicit_environment_opt_in(monkeypatch):
    monkeypatch.delenv("DEV_TOOLS", raising=False)
    assert settings()["DEV_TOOLS"] is False
    monkeypatch.setenv("DEV_TOOLS", "true")
    assert settings()["DEV_TOOLS"] is False
    monkeypatch.setenv("DEV_TOOLS", "1")
    assert settings()["DEV_TOOLS"] is True


def test_dev_tools_setting_must_be_a_boolean(json_config):
    with pytest.raises(ValueError, match="DEV_TOOLS must be a boolean"):
        create_app({**json_config, "DEV_TOOLS": "0"})


def test_dev_tools_need_the_postgres_driver(json_config):
    with pytest.raises(ValueError, match="DEV_TOOLS requires STORAGE_DRIVER=postgres"):
        create_app({**json_config, "DEV_TOOLS": True})


@pytest.mark.parametrize("method, path", ROUTES)
def test_routes_do_not_exist_when_the_setting_is_off(json_config, method, path):
    application = create_app(json_config)
    assert "dev" not in application.blueprints
    assert not [rule for rule in application.url_map.iter_rules() if "/dev" in rule.rule]
    client = application.test_client()
    client.post("/v1/auth/register", base_url=BASE,
                json={"username": "alice", "password": "throwaway"})
    response = getattr(client, method)(path, base_url=BASE, headers=DEV, json={"role": "admin"})
    assert response.status_code == 404
    assert client.get("/v1/auth/session", base_url=BASE).get_json()["session"]["isAdmin"] is False


@pytest.mark.parametrize("method, path", ROUTES)
@pytest.mark.parametrize("request_from", [
    {"environ_overrides": {"REMOTE_ADDR": "192.168.1.20"}},
    {"base_url": "http://192.168.1.10:5000"},
    {"base_url": "http://attacker.example:5000"},  # DNS rebinding: loopback peer, foreign host
    {"headers": {**DEV, "X-Forwarded-For": "127.0.0.1"}},
])
def test_only_direct_loopback_requests_reach_the_tool(config, method, path, request_from):
    client = Client(config)
    register(client, "alice")
    keywords = {"headers": DEV, "json": {"role": "admin"}, **request_from}
    assert getattr(client, method)(path, **keywords).status_code == 404
    assert [user["role"] for user in client.get("/dev/api/users").get_json()["users"]] == ["user"]


def test_users_are_listed_sorted_without_password_hashes(config):
    client = Client(config)
    for name in ("carol", "alice", "Bob"):
        register(client, name)
    response = client.get("/dev/api/users")
    assert response.status_code == 200
    users = response.get_json()["users"]
    assert [user["username"] for user in users] == ["Bob", "alice", "carol"]
    assert all(set(user) == {"username", "role", "createdAt"} for user in users)
    assert users[0]["role"] == "user"
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}", users[0]["createdAt"])
    assert "hash" not in response.get_data(as_text=True)


def test_role_change_applies_to_the_existing_session_at_the_next_request(config):
    client = Client(config)
    register(client, "alice")
    assert client.get("/v1/metrics").status_code == 403
    response = set_role(client, "alice", "admin")
    assert response.status_code == 200
    assert response.get_json()["user"]["role"] == "admin"
    assert response.get_json()["user"]["username"] == "alice"
    assert client.get("/v1/auth/session").get_json()["session"]["isAdmin"] is True
    assert client.get("/v1/metrics").status_code == 200
    assert set_role(client, "alice", "user").get_json()["user"]["role"] == "user"
    assert client.get("/v1/metrics").status_code == 403


@pytest.mark.parametrize("body", [{"role": "root"}, {"role": None}, {"role": "admin", "x": 1},
                                  {}, ["admin"], "admin"])
def test_invalid_role_is_rejected_without_changes(config, body):
    client = Client(config)
    register(client, "alice")
    response = client.put("/dev/api/users/alice/role", json=body, headers=DEV)
    assert response.status_code == 400
    assert response.get_json() == {"error": "invalid"}
    assert client.get("/dev/api/users").get_json()["users"][0]["role"] == "user"


def test_unknown_user_is_not_found(config):
    client = Client(config)
    register(client, "alice")
    assert set_role(client, "ghost", "admin").status_code == 404
    assert client.delete("/dev/api/users/ghost", headers=DEV).status_code == 404
    assert set_role(client, "Alice", "admin").status_code == 404  # names are case-sensitive
    assert len(client.get("/dev/api/users").get_json()["users"]) == 1


def test_usernames_with_reserved_characters_are_addressable(config):
    client = Client(config)
    register(client, "a b/c?d")
    assert set_role(client, "a%20b%2Fc%3Fd", "admin").get_json()["user"]["username"] == "a b/c?d"
    assert client.delete("/dev/api/users/a%20b%2Fc%3Fd", headers=DEV).status_code == 200
    assert client.get("/dev/api/users").get_json() == {"users": []}


def test_state_changes_reject_what_another_origin_can_send_without_a_preflight(config):
    client = Client(config)
    register(client, "alice")
    body = json.dumps({"role": "admin"})
    path = "/dev/api/users/alice/role"
    rejected = [
        client.put(path, json={"role": "admin"}),  # no custom header
        client.put(path, json={"role": "admin"}, headers={"X-Sereno-Dev": "0"}),
        client.put(path, data=body, headers=DEV, content_type="text/plain"),
        client.put(path, data=body, headers=DEV,
                   content_type="application/x-www-form-urlencoded"),
        client.put(path, data=body, headers=DEV),  # no content type
        client.delete("/dev/api/users/alice"),
        # The verbs an HTML form can send do not reach the operations at all.
        client.post(path, json={"role": "admin"}, headers=DEV),
        client.post("/dev/api/users/alice", headers=DEV),
        client.get(path + "?role=admin", headers=DEV),
    ]
    assert [response.status_code for response in rejected] == [403, 403, 403, 403, 403, 403,
                                                               405, 405, 405]
    assert rejected[0].get_json() == {"error": "forbidden"}
    assert client.get("/dev/api/users").get_json()["users"] == [
        {"username": "alice", "role": "user",
         "createdAt": client.get("/dev/api/users").get_json()["users"][0]["createdAt"]}]


@pytest.mark.parametrize("method, path", ROUTES)
def test_no_cors_allowance_is_ever_sent(config, method, path):
    client = Client(config)
    register(client, "alice")
    origin = {"Origin": "https://attacker.example",
              "Access-Control-Request-Method": method.upper(),
              "Access-Control-Request-Headers": "content-type, x-sereno-dev"}
    for response in (client.options(path, headers=origin),
                     getattr(client, method)(path, headers={**DEV, **origin},
                                             json={"role": "user"})):
        assert not [name for name in response.headers.keys()
                    if name.lower().startswith("access-control-")]


def test_page_is_self_contained_spanish_and_cannot_be_framed(config):
    client = Client(config)
    register(client, "<img src=x onerror=alert(1)>")
    response = client.get("/dev/users")
    assert response.status_code == 200
    assert response.mimetype == "text/html" and "charset=utf-8" in response.content_type
    page = response.get_data(as_text=True)
    assert "Herramienta de desarrollo" in page and '<html lang="es">' in page
    # No external assets: nothing is loaded from a URL.
    assert not re.search(r"""(src|href)\s*=|url\(|@import|https?://""", page)
    # The page is static: user data arrives through the API and is set as text.
    assert "onerror" not in page and "innerHTML" not in page and "textContent" in page
    assert response.headers["X-Frame-Options"] == "DENY"
    assert response.headers["Cache-Control"] == "no-store"
    policy = response.headers["Content-Security-Policy"]
    assert "default-src 'none'" in policy and "frame-ancestors 'none'" in policy


def test_page_module_has_no_template_placeholders():
    source = (Path(__file__).resolve().parent.parent / "app" / "dev" / "page.py").read_text(
        encoding="utf-8")
    assert "{{" not in source and "%s" not in source and ".format(" not in source


def test_deletion_removes_sessions_unlinks_queries_and_keeps_anonymous_responses(
        config, database):
    alice, bob = Client(config), Client(config)
    register(alice, "alice")
    register(bob, "bob")
    for client in (alice, bob):
        client.post("/v1/verdict", json={"url": "https://tienda-servicios.pe/"})
        client.post("/v1/survey", json={"answers": [1, 2, 3, 4, 5, 1, 2, 3, 4, 5]})
    assert set_role(bob, "bob", "admin").status_code == 200

    assert alice.delete("/dev/api/users/alice", headers=DEV).status_code == 200

    assert alice.get("/v1/auth/session").get_json() == {"session": None}
    assert bob.get("/v1/auth/session").get_json()["session"]["isAdmin"] is True
    assert bob.get("/v1/metrics").get_json()["metrics"]["analyzed"] == 2
    with database.transaction(RuntimeError) as connection:
        assert connection.execute(
            "SELECT (SELECT count(*) FROM usuario), (SELECT count(*) FROM sesion), "
            "(SELECT count(*) FROM consulta), (SELECT count(*) FROM consulta WHERE id_usuario "
            "IS NULL), (SELECT count(*) FROM prediccion), (SELECT count(*) FROM "
            "encuesta_participante), (SELECT count(*) FROM encuesta_respuesta)").fetchone() == (
                1, 1, 2, 1, 1, 1, 2)
    register(alice, "alice")
    assert alice.get("/v1/history").get_json() == {"entries": []}
    assert alice.get("/v1/survey").get_json() == {"submitted": False}
