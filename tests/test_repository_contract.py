from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import pytest

from app.auth.domain import Session, StorageError, User
from app.auth.postgres_repository import PostgresAuthRepository


@pytest.fixture
def repository(database):
    return PostgresAuthRepository(database)


USER = User("alice", "hash", "user", "2026-10-27T12:30")
SESSION = Session("alice", "2026-10-27T12:30", "initial")


def test_missing_records_and_idempotent_revocation(repository):
    assert repository.get_user("missing") is None
    assert repository.get_session("missing") is None
    repository.delete_session("missing")


def test_registration_duplicate_and_case_sensitive_identity(repository):
    assert repository.register(USER, SESSION) is True
    assert repository.register(replace(USER, password_hash="other"),
                               replace(SESSION, token="duplicate")) is False
    assert repository.get_user("alice") == USER
    assert repository.get_session("initial") == SESSION
    assert repository.get_session("duplicate") is None
    assert repository.register(replace(USER, username="Alice"),
                               replace(SESSION, username="Alice", token="case")) is True


def test_multiple_sessions_and_selective_revocation(repository):
    repository.register(USER, SESSION)
    second = replace(SESSION, token="second")
    repository.add_session(second)
    repository.delete_session("initial")
    repository.delete_session("initial")
    assert repository.get_session("initial") is None
    assert repository.get_session("second") == second
    assert repository.get_user("alice") == USER


@pytest.mark.parametrize("session_owner", ["alice", "missing"])
def test_invalid_reference_does_not_partially_register(repository, session_owner):
    repository.register(USER, SESSION)
    with pytest.raises(StorageError):
        repository.register(replace(USER, username="bob"),
                            replace(SESSION, username=session_owner, token="bad"))
    assert repository.get_user("bob") is None
    assert repository.get_session("bad") is None
    assert repository.get_session("initial") == SESSION


def test_orphan_session_is_rejected_without_changes(repository):
    with pytest.raises(StorageError):
        repository.add_session(replace(SESSION, username="missing"))
    assert repository.get_session("initial") is None


def test_existing_token_is_replaced(repository):
    repository.register(USER, SESSION)
    updated = replace(SESSION, loginAt="2026-10-27T12:31")
    repository.add_session(updated)
    assert repository.get_session("initial") == updated
    bob = replace(USER, username="bob")
    bob_session = replace(SESSION, username="bob")
    assert repository.register(bob, bob_session) is True
    assert repository.get_session("initial") == bob_session
    assert repository.get_user("alice") == USER


def test_records_do_not_expose_live_state(repository):
    repository.register(USER, SESSION)
    user = repository.get_user("alice")
    session = repository.get_session("initial")
    # Even bypassing frozen assignment must not mutate repository-owned records.
    object.__setattr__(user, "password_hash", "changed")
    object.__setattr__(session, "username", "changed")
    assert repository.get_user("alice") == USER
    assert repository.get_session("initial") == SESSION


def test_input_records_are_copied(repository):
    user, session = replace(USER), replace(SESSION)
    repository.register(user, session)
    object.__setattr__(user, "password_hash", "changed")
    object.__setattr__(session, "username", "changed")
    second = replace(SESSION, token="second")
    repository.add_session(second)
    object.__setattr__(second, "username", "changed")
    assert repository.get_user("alice") == USER
    assert repository.get_session("initial") == SESSION
    assert repository.get_session("second") == replace(SESSION, token="second")


def test_concurrent_duplicate_registration_is_atomic(repository):
    def attempt(index):
        token = f"session-{index}"
        return token, repository.register(USER, replace(SESSION, token=token))

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(attempt, range(4)))
    assert sum(created for _, created in results) == 1
    for token, created in results:
        assert (repository.get_session(token) is not None) == created


# Account administration (development tools)

def test_users_are_listed_by_name_as_snapshots(repository):
    assert repository.list_users() == ()
    for name in ("carol", "alice", "Bob"):
        repository.register(replace(USER, username=name),
                            replace(SESSION, username=name, token="token-" + name))
    listed = repository.list_users()
    assert [user.username for user in listed] == ["Bob", "alice", "carol"]
    assert listed[1] == USER
    object.__setattr__(listed[1], "role", "admin")
    assert repository.get_user("alice") == USER


def test_role_change_keeps_the_account_and_its_sessions(repository):
    repository.register(USER, SESSION)
    assert repository.set_role("alice", "admin") is True
    assert repository.get_user("alice") == replace(USER, role="admin")
    assert repository.get_session("initial") == SESSION
    assert repository.set_role("alice", "admin") is True
    assert repository.set_role("alice", "user") is True
    assert repository.get_user("alice") == USER
    assert repository.set_role("missing", "admin") is False
    assert repository.set_role("Alice", "admin") is False
    assert repository.get_user("missing") is None


@pytest.mark.parametrize("role", ["root", "", None, "ADMIN"])
def test_role_outside_the_domain_is_rejected_without_changes(repository, role):
    repository.register(USER, SESSION)
    with pytest.raises(StorageError):
        repository.set_role("alice", role)
    assert repository.get_user("alice") == USER


def test_deleting_a_user_removes_its_sessions_and_nothing_else(repository):
    repository.register(USER, SESSION)
    repository.add_session(replace(SESSION, token="second"))
    bob = replace(USER, username="bob")
    bob_session = replace(SESSION, username="bob", token="bob-token")
    repository.register(bob, bob_session)
    assert repository.delete_user("alice") is True
    assert repository.get_user("alice") is None
    assert repository.get_session("initial") is None
    assert repository.get_session("second") is None
    assert repository.list_users() == (bob,)
    assert repository.get_session("bob-token") == bob_session
    assert repository.delete_user("alice") is False
    assert repository.delete_user("Bob") is False
    # The name can be registered again, with no session inherited.
    assert repository.register(USER, replace(SESSION, token="fresh")) is True
    assert repository.get_session("initial") is None
