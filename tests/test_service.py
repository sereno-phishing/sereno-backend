import ast
import inspect
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from app.auth import domain
from app.auth.domain import DuplicateUser, InvalidCredentials, PasswordVerificationError
from app.auth.service import AuthService


@pytest.fixture
def service():
    from app.auth.memory_repository import MemoryAuthRepository

    tokens = iter(["initial", "login", "third"])
    return AuthService(MemoryAuthRepository(), lambda value: "hash:" + value,
                       lambda hashed, value: hashed == "hash:" + value,
                       clock=lambda: "2026-10-27T12:30",
                       token_factory=lambda: next(tokens))


def test_credentials_are_normalized_and_immutable():
    credentials = domain.Credentials(" alice ", " ")
    assert credentials.username == "alice"
    assert credentials.password == " "
    with pytest.raises(FrozenInstanceError):
        credentials.username = "bob"


def test_use_case_signatures_require_typed_credentials():
    for action in (AuthService.register, AuthService.login):
        parameters = inspect.signature(action).parameters
        assert list(parameters) == ["self", "credentials"]
        assert parameters["credentials"].annotation is domain.Credentials


@pytest.mark.parametrize("username,password", [
    (None, "p"), (5, "p"), ("a", None), ("a", 5), (" ", "p"),
    ("a", ""), ("\ud800", "p"), ("a", "\ud800"),
])
def test_non_http_credentials_enforce_invariants(username, password):
    with pytest.raises(InvalidCredentials):
        domain.Credentials(username, password)


def test_typed_registration_login_and_selective_logout(service):
    credentials = domain.Credentials(" alice ", "secret")
    user, token = service.register(credentials)
    assert user == {"username": "alice", "isAdmin": False, "createdAt": "2026-10-27T12:30"}
    assert token == "initial"
    assert service.repository.get_user("alice").password_hash == "hash:secret"
    assert service.login(credentials) == (user, "login")
    assert service.session("login") == {"username": "alice", "isAdmin": False,
                                        "loginAt": "2026-10-27T12:30", "token": "login"}
    service.logout(token)
    service.logout(token)
    service.logout(None)
    assert service.session(token) is None
    assert service.session(None) is None
    assert service.session("missing") is None
    assert service.session("login") is not None


def test_duplicate_and_invalid_login(service):
    credentials = domain.Credentials("alice", "secret")
    service.register(credentials)
    with pytest.raises(DuplicateUser):
        service.register(credentials)
    for invalid in (domain.Credentials("Alice", "secret"), domain.Credentials("alice", "bad")):
        with pytest.raises(InvalidCredentials):
            service.login(invalid)
    assert service.repository.get_session("login") is None


def test_whitespace_password_and_verifier_failure(service):
    credentials = domain.Credentials("alice", " ")
    service.register(credentials)
    assert service.login(credentials)[1] == "login"

    def broken_verifier(hashed, password):
        raise ValueError("unusable hash")

    service.verify_password = broken_verifier
    with pytest.raises(PasswordVerificationError):
        service.login(credentials)
    assert service.repository.get_session("third") is None


def test_core_imports_only_standard_library_and_inward_ports():
    import sys

    allowed_local = {"domain", "repository"}
    for name in ("domain", "repository", "service"):
        tree = ast.parse((Path(__file__).parents[1] / "app" / "auth" / f"{name}.py").read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                if node.level:
                    assert node.module in allowed_local
                else:
                    assert node.module.split(".")[0] in sys.stdlib_module_names
                    assert node.module.split(".")[0] not in {"os", "pathlib", "tempfile", "sqlite3"}
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    assert alias.name.split(".")[0] in sys.stdlib_module_names
                    assert alias.name.split(".")[0] not in {"os", "pathlib", "tempfile", "sqlite3"}
