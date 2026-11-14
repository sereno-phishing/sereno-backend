import json

import pytest

from app import create_app
from app.policy.domain import InvalidPolicy, PolicyRow, PolicyStorageError
from app.policy.postgres_repository import PostgresPolicyRepository
from app.policy.service import PolicyService
from conftest import rows, set_role


def register(client, username="alice", **extra):
    return client.post("/v1/auth/register", json={
        "username": username,
        "password": "throwaway",
        **extra,
    })


def make_admin(client, config, username="alice"):
    response = register(client, username)
    assert response.status_code == 201
    set_role(config, username, "admin")
    return response.get_json()["token"]


@pytest.mark.parametrize("method", ["get", "put"])
def test_domain_policies_require_a_valid_session(config, method):
    client = create_app(config).test_client()
    before = rows(config, "politica_dominio")
    kwargs = {"json": {"domains": []}} if method == "put" else {}

    response = getattr(client, method)("/v1/domains", **kwargs)

    assert response.status_code == 401
    assert response.get_json() == {"error": "invalid"}
    assert rows(config, "politica_dominio") == before


@pytest.mark.parametrize("method", ["get", "put"])
def test_domain_policies_forbid_nonadministrators(config, method):
    client = create_app(config).test_client()
    assert register(client).status_code == 201
    before = rows(config, "politica_dominio")
    kwargs = {"json": {"domains": []}} if method == "put" else {}

    response = getattr(client, method)("/v1/domains", **kwargs)

    assert response.status_code == 403
    assert response.get_json() == {"error": "forbidden"}
    assert rows(config, "politica_dominio") == before


def test_existing_session_observes_manual_promotion_and_demotion(config):
    client = create_app(config).test_client()
    token = register(client).get_json()["token"]
    assert client.get("/v1/domains").status_code == 403

    set_role(config, "alice", "admin")
    promoted = client.get("/v1/domains")
    assert promoted.status_code == 200
    assert promoted.get_json() == {"domains": []}
    assert client.get("/v1/auth/session").get_json()["session"]["isAdmin"] is True

    set_role(config, "alice", "user")
    demoted = client.put("/v1/domains", json={"domains": []})
    assert demoted.status_code == 403
    assert demoted.get_json() == {"error": "forbidden"}
    assert client.get("/v1/auth/session").get_json()["session"]["isAdmin"] is False
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
    assert recreated.get("/v1/domains").get_json() == replacement


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
def test_invalid_policy_payload_leaves_the_stored_list_unchanged(config, payload):
    client = create_app(config).test_client()
    make_admin(client, config)
    valid = {"domains": [
        {"domain": "kept.example", "policy": "advertencia", "updatedAt": "2026-10-27"},
    ]}
    assert client.put("/v1/domains", json=valid).status_code == 200
    before = rows(config, "politica_dominio")

    response = client.put("/v1/domains", data=json.dumps(payload), content_type="application/json")

    assert response.status_code == 400
    assert response.get_json() == {"error": "invalid"}
    assert rows(config, "politica_dominio") == before


def test_policy_domain_defaults_only_when_explicitly_requested():
    assert PolicyRow.create(" Example.com ", None, "2026-10-27", default_policy=True) == PolicyRow(
        "example.com", "advertencia", "2026-10-27")
    with pytest.raises(InvalidPolicy):
        PolicyRow.create("example.com", None, "2026-10-27")




@pytest.mark.parametrize("operation", ["list", "replace"])
def test_policy_storage_failure_is_controlled_and_keeps_the_list(config, monkeypatch, operation):
    def fail(*arguments):
        raise PolicyStorageError("Storage unavailable: /private/location")

    client = create_app(config).test_client()
    make_admin(client, config)
    before = rows(config, "politica_dominio")
    monkeypatch.setattr(PostgresPolicyRepository, operation, fail)

    method, kwargs = {"list": (client.get, {}),
                      "replace": (client.put, {"json": {"domains": []}})}[operation]
    response = method("/v1/domains", **kwargs)

    assert response.status_code == 500
    assert response.get_json() == {"error": "server"}
    assert "/private/location" not in response.get_data(as_text=True)
    assert rows(config, "politica_dominio") == before


def test_policy_for_a_domain_is_its_row_or_the_default(database):
    service = PolicyService(PostgresPolicyRepository(database))
    assert service.policy_for("shop.pe") == "advertencia"
    service.replace((PolicyRow("shop.pe", "bloqueo", "2026-11-13"),
                     PolicyRow("www.bank.pe", "bloqueo", "2026-11-13")))
    assert service.policy_for("shop.pe") == "bloqueo"
    # Verdict domains carry no leading "www."; a row typed with it still applies.
    assert service.policy_for("bank.pe") == "bloqueo"
    assert service.policy_for("other.pe") == "advertencia"
