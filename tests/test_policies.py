import json
from pathlib import Path

import pytest

from app import create_app
from app.policy.domain import InvalidPolicy, PolicyRow, PolicyStorageError
from app.policy.json_repository import JsonPolicyRepository
from app.policy.service import PolicyService


@pytest.fixture
def config(tmp_path):
    return {
        "TESTING": True,
        "DATA_FILE": str(tmp_path / "auth.json"),
        "POLICY_DATA_FILE": str(tmp_path / "policies.json"),
    }


def policy_path(config):
    return Path(config["POLICY_DATA_FILE"])


def register(client, username="alice", **extra):
    return client.post("/v1/auth/register", json={
        "username": username,
        "password": "throwaway",
        **extra,
    })


def set_role(config, username, role):
    path = Path(config["DATA_FILE"])
    data = json.loads(path.read_text(encoding="utf-8"))
    data["users"][username]["role"] = role
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def make_admin(client, config, username="alice"):
    response = register(client, username)
    assert response.status_code == 201
    set_role(config, username, "administrador")
    return response.get_json()["token"]


@pytest.mark.parametrize("method", ["get", "put"])
def test_domain_policies_require_a_valid_session(config, method):
    client = create_app(config).test_client()
    before = policy_path(config).read_bytes()
    kwargs = {"json": {"domains": []}} if method == "put" else {}

    response = getattr(client, method)("/v1/domains", **kwargs)

    assert response.status_code == 401
    assert response.get_json() == {"error": "invalid"}
    assert policy_path(config).read_bytes() == before


@pytest.mark.parametrize("method", ["get", "put"])
def test_domain_policies_forbid_nonadministrators(config, method):
    client = create_app(config).test_client()
    assert register(client).status_code == 201
    before = policy_path(config).read_bytes()
    kwargs = {"json": {"domains": []}} if method == "put" else {}

    response = getattr(client, method)("/v1/domains", **kwargs)

    assert response.status_code == 403
    assert response.get_json() == {"error": "forbidden"}
    assert policy_path(config).read_bytes() == before


def test_existing_session_observes_manual_promotion_and_demotion(config):
    client = create_app(config).test_client()
    token = register(client).get_json()["token"]
    assert client.get("/v1/domains").status_code == 403

    set_role(config, "alice", "administrador")
    promoted = client.get("/v1/domains")
    assert promoted.status_code == 200
    assert promoted.get_json() == {"domains": []}
    assert client.get("/v1/auth/session").get_json()["session"]["role"] == "administrador"

    set_role(config, "alice", "usuario")
    demoted = client.put("/v1/domains", json={"domains": []})
    assert demoted.status_code == 403
    assert demoted.get_json() == {"error": "forbidden"}
    assert client.get("/v1/auth/session").get_json()["session"]["role"] == "usuario"
    assert token


def test_administrator_replaces_whole_policy_list_and_recreation_persists(config):
    client = create_app(config).test_client()
    token = make_admin(client, config)
    payload = {"domains": [
        {"domain": "  Shop.Example.COM  ", "policy": "bloqueo", "updatedAt": "2026-10-27"},
        {"domain": "login.example.com", "policy": "advertencia", "updatedAt": "2026-10-28"},
    ]}

    response = client.put("/v1/domains", json=payload)

    expected = {"domains": [
        {"domain": "shop.example.com", "policy": "bloqueo", "updatedAt": "2026-10-27"},
        {"domain": "login.example.com", "policy": "advertencia", "updatedAt": "2026-10-28"},
    ]}
    assert response.status_code == 200
    assert response.get_json() == expected
    assert client.get("/v1/domains").get_json() == expected

    recreated = create_app(config).test_client()
    recreated.set_cookie("sereno_session", token)
    assert recreated.get("/v1/domains").get_json() == expected

    replacement = {"domains": [
        {"domain": "new.example", "policy": "advertencia", "updatedAt": "2026-10-29"},
    ]}
    assert recreated.put("/v1/domains", json=replacement).get_json() == replacement
    assert json.loads(policy_path(config).read_text(encoding="utf-8")) == replacement


@pytest.mark.parametrize("payload", [
    None,
    [],
    {},
    {"domains": [], "extra": True},
    {"domains": "example.com"},
    {"domains": [{}]},
    {"domains": [{"domain": "example.com", "policy": "advertencia", "updatedAt": "2026-10-27", "extra": 1}]},
    {"domains": [{"domain": 3, "policy": "advertencia", "updatedAt": "2026-10-27"}]},
    {"domains": [{"domain": "", "policy": "advertencia", "updatedAt": "2026-10-27"}]},
    {"domains": [{"domain": "https://example.com", "policy": "advertencia", "updatedAt": "2026-10-27"}]},
    {"domains": [{"domain": "example.com/path", "policy": "advertencia", "updatedAt": "2026-10-27"}]},
    {"domains": [{"domain": "example.com:443", "policy": "advertencia", "updatedAt": "2026-10-27"}]},
    {"domains": [{"domain": "example.com", "policy": "permitir", "updatedAt": "2026-10-27"}]},
    {"domains": [{"domain": "example.com", "policy": [], "updatedAt": "2026-10-27"}]},
    {"domains": [{"domain": "example.com", "policy": "admin", "updatedAt": "2026-10-27"}]},
    {"domains": [{"domain": "example.com", "policy": "advertencia", "updatedAt": "27-10-2026"}]},
    {"domains": [{"domain": "example.com", "policy": "advertencia", "updatedAt": "2026-02-30"}]},
    {"domains": [
        {"domain": "Example.com", "policy": "advertencia", "updatedAt": "2026-10-27"},
        {"domain": " example.COM ", "policy": "bloqueo", "updatedAt": "2026-10-28"},
    ]},
])
def test_invalid_policy_payload_leaves_persisted_bytes_unchanged(config, payload):
    client = create_app(config).test_client()
    make_admin(client, config)
    valid = {"domains": [
        {"domain": "kept.example", "policy": "advertencia", "updatedAt": "2026-10-27"},
    ]}
    assert client.put("/v1/domains", json=valid).status_code == 200
    before = policy_path(config).read_bytes()

    response = client.put("/v1/domains", data=json.dumps(payload), content_type="application/json")

    assert response.status_code == 400
    assert response.get_json() == {"error": "invalid"}
    assert policy_path(config).read_bytes() == before


@pytest.mark.parametrize("content", [
    "broken",
    "[]",
    "{}",
    '{"domains": {}}',
    '{"domains": [{"domain":"example.com","policy":"permitir","updatedAt":"2026-10-27"}]}',
    '{"domains": [{"domain":"Example.com","policy":"advertencia","updatedAt":"2026-10-27"}]}',
    '{"domains": [{"domain":"example.com","policy":"advertencia","updatedAt":"not-a-date"}]}',
])
def test_corrupt_existing_policy_data_is_rejected_and_preserved(tmp_path, content):
    path = tmp_path / "policies.json"
    path.write_text(content, encoding="utf-8")

    with pytest.raises(PolicyStorageError, match="Storage unavailable"):
        JsonPolicyRepository(path)

    assert path.read_text(encoding="utf-8") == content


def test_policy_storage_failure_is_controlled_and_preserves_bytes(config):
    client = create_app(config).test_client()
    make_admin(client, config)
    path = policy_path(config)
    path.write_text("corrupt /private/location", encoding="utf-8")

    for method, kwargs in ((client.get, {}), (client.put, {"json": {"domains": []}})):
        response = method("/v1/domains", **kwargs)
        assert response.status_code == 500
        assert response.get_json() == {"error": "server"}
        assert "/private/location" not in response.get_data(as_text=True)
        assert path.read_text(encoding="utf-8") == "corrupt /private/location"


def test_failed_atomic_policy_replace_keeps_original_and_cleans_temporary(tmp_path, monkeypatch):
    import app.policy.json_repository as adapter

    path = tmp_path / "policies.json"
    repository = JsonPolicyRepository(path)
    service = PolicyService(repository)
    before = path.read_bytes()

    def fail(*args):
        raise OSError("private path")

    monkeypatch.setattr(adapter.os, "replace", fail)
    with pytest.raises(PolicyStorageError, match="Storage unavailable"):
        service.replace((PolicyRow("example.com", "bloqueo", "2026-10-27"),))
    assert path.read_bytes() == before
    assert list(tmp_path.iterdir()) == [path]


def test_policy_path_resolves_against_backend_root_or_auth_file(tmp_path, monkeypatch):
    from app.config import BACKEND_ROOT, policy_data_path

    auth_path = tmp_path / "nested" / "auth.json"
    monkeypatch.chdir(tmp_path)
    assert policy_data_path(auth_path) == auth_path.resolve().with_name("policies.json")
    assert policy_data_path(auth_path, "custom/policies.json") == BACKEND_ROOT / "custom/policies.json"


def test_policy_domain_defaults_only_when_explicitly_requested():
    assert PolicyRow.create(" Example.com ", None, "2026-10-27", default_policy=True) == PolicyRow(
        "example.com", "advertencia", "2026-10-27")
    with pytest.raises(InvalidPolicy):
        PolicyRow.create("example.com", None, "2026-10-27")
