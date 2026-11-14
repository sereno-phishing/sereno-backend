"""The policy and survey repository ports, exercised against every adapter."""
from concurrent.futures import ThreadPoolExecutor

import pytest

from app.policy.domain import PolicyRow, PolicyStorageError
from app.policy.json_repository import JsonPolicyRepository
from app.policy.service import PolicyService
from app.survey.domain import AlreadySubmitted, SurveyStorageError, SurveySubmission
from app.survey.json_repository import JsonSurveyRepository


ACCOUNTS = ("alice", "bob", "carol")
FIRST = SurveySubmission((1, 2, 3, 4, 5, 1, 2, 3, 4, 5), "2026-11-13T10:00:00Z")
SECOND = SurveySubmission((5, 5, 5, 5, 5, 1, 1, 1, 1, 1), "2026-11-13T10:00:01Z")
ROWS = (PolicyRow.create("shop.example.com", "bloqueo", "2026-10-27"),
        PolicyRow.create("another.pe", "advertencia", "2026-11-01"))


def add_accounts(database):
    with database.transaction(RuntimeError) as connection:
        for name in ACCOUNTS:
            connection.execute(
                "INSERT INTO usuario (nombre, hash_contrasena, rol, fecha_registro) "
                "VALUES (%s, 'hash', 'user', now())", (name,))


@pytest.fixture(params=["json", "postgres"])
def surveys(request, tmp_path):
    if request.param == "json":
        return JsonSurveyRepository(tmp_path / "surveys.json")
    from app.survey.postgres_repository import PostgresSurveyRepository

    database = request.getfixturevalue("database")
    add_accounts(database)
    return PostgresSurveyRepository(database)


@pytest.fixture(params=["json", "postgres"])
def policies(request, tmp_path):
    if request.param == "json":
        return JsonPolicyRepository(tmp_path / "policies.json")
    from app.policy.postgres_repository import PostgresPolicyRepository

    return PostgresPolicyRepository(request.getfixturevalue("database"))


# Survey

def test_one_submission_per_account_and_separate_participation(surveys):
    assert surveys.has_submitted("alice") is False
    assert surveys.list_submissions() == ()
    surveys.submit("alice", FIRST)
    assert surveys.has_submitted("alice") is True
    assert surveys.has_submitted("bob") is False
    surveys.submit("bob", SECOND)
    assert surveys.list_submissions() == (FIRST, SECOND)


def test_a_second_submission_is_rejected_without_adding_a_response(surveys):
    surveys.submit("alice", FIRST)
    with pytest.raises(AlreadySubmitted):
        surveys.submit("alice", SECOND)
    assert surveys.list_submissions() == (FIRST,)
    assert surveys.has_submitted("alice") is True


@pytest.mark.parametrize("username, submission", [
    ("", FIRST), (None, FIRST), ("alice", {"answers": [1] * 10}), ("alice", None)])
def test_invalid_survey_input_is_a_storage_error_without_changes(surveys, username, submission):
    with pytest.raises(SurveyStorageError):
        surveys.submit(username, submission)
    assert surveys.list_submissions() == ()
    assert surveys.has_submitted("alice") is False


def test_invalid_username_cannot_be_checked(surveys):
    with pytest.raises(SurveyStorageError):
        surveys.has_submitted("")


def test_concurrent_submissions_of_one_account_keep_a_single_response(surveys):
    def attempt(_):
        try:
            surveys.submit("carol", FIRST)
            return True
        except AlreadySubmitted:
            return False

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(attempt, range(4)))
    assert sum(results) == 1
    assert surveys.list_submissions() == (FIRST,)


def test_postgres_responses_cannot_be_linked_to_participants(database):
    from app.survey.postgres_repository import PostgresSurveyRepository

    add_accounts(database)
    surveys = PostgresSurveyRepository(database)
    surveys.submit("alice", FIRST)
    with database.transaction(RuntimeError) as connection:
        def columns(table):
            return {row[0] for row in connection.execute(
                "SELECT column_name FROM information_schema.columns WHERE table_name = %s",
                (table,))}
        # No user, date or sequence shared between the two tables.
        assert columns("encuesta_respuesta") == {"id_respuesta", "respuestas", "fecha_envio"}
        assert columns("encuesta_participante") == {"id_usuario"}


def test_postgres_rejects_a_participant_without_an_account(database):
    from app.survey.postgres_repository import PostgresSurveyRepository

    surveys = PostgresSurveyRepository(database)
    with pytest.raises(SurveyStorageError):
        surveys.submit("ghost", FIRST)
    assert surveys.list_submissions() == ()
    assert surveys.has_submitted("ghost") is False


# Policies

def test_policies_are_replaced_as_a_whole_and_keep_their_order(policies):
    assert policies.list() == ()
    policies.replace(ROWS)
    assert policies.list() == ROWS
    reordered = (ROWS[1], PolicyRow.create("third.pe", "bloqueo", "2026-11-13"))
    policies.replace(reordered)
    assert policies.list() == reordered
    policies.replace(())
    assert policies.list() == ()


@pytest.mark.parametrize("rows", [list(ROWS), ({"domain": "x.pe"},), None,
                                  (ROWS[0], ROWS[0])])
def test_invalid_or_duplicated_policies_change_nothing(policies, rows):
    policies.replace(ROWS)
    with pytest.raises(PolicyStorageError):
        policies.replace(rows)
    assert policies.list() == ROWS


def test_policy_lookup_reads_the_current_rows(policies):
    service = PolicyService(policies)
    assert service.policy_for("shop.example.com") == "advertencia"
    policies.replace((ROWS[0], PolicyRow.create("www.tienda.pe", "bloqueo", "2026-11-13")))
    assert service.policy_for("shop.example.com") == "bloqueo"
    assert service.policy_for("tienda.pe") == "bloqueo"
    assert service.policy_for("unlisted.pe") == "advertencia"


def test_concurrent_replacements_leave_one_complete_list(policies):
    lists = [tuple(PolicyRow.create(f"d{index}-{item}.pe", "bloqueo", "2026-11-13")
                   for item in range(5)) for index in range(4)]
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(policies.replace, lists))
    assert policies.list() in lists
