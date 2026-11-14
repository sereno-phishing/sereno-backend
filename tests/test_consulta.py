"""History and query-log ports against every adapter, and the PostgreSQL
`consulta` model where one row per served verdict backs both."""
from datetime import datetime, timedelta, timezone

import pytest

from app.cache.memory_repository import MemoryCacheRepository
from app.cache.service import CacheService
from app.history.domain import CAPACITY, HistoryRecord, HistoryStorageError
from app.history.json_repository import JsonHistoryRepository
from app.history.service import HistoryService
from app.metrics.domain import QueryEvent, QueryLogStorageError
from app.metrics.json_repository import JsonQueryLogRepository
from app.metrics.service import MetricsService
from app.verdict.classifier import Classification
from app.verdict.service import VerdictService
from app.verdict.url import cache_key, normalize


LIMA = timezone(timedelta(hours=-5))
START = datetime(2026, 10, 31, 9, 30, 20, tzinfo=LIMA)


class Clock:
    def __init__(self, value=START):
        self.value = value

    def __call__(self):
        return self.value

    def advance(self, **delta):
        self.value += timedelta(**delta)


class SpyClassifier:
    version = "9.9"

    def __init__(self):
        self.calls = []

    def classify(self, url):
        self.calls.append(url)
        return Classification("phishing", 0.81)


class SpySideEffect:
    def __init__(self):
        self.records = []

    def record(self, *arguments):
        self.records.append(arguments)


def add_accounts(database, *names):
    with database.transaction(RuntimeError) as connection:
        for name in names:
            connection.execute(
                "INSERT INTO usuario (nombre, hash_contrasena, rol, fecha_registro) "
                "VALUES (%s, 'hash', 'user', now())", (name,))


def count(database, query):
    with database.transaction(RuntimeError) as connection:
        return connection.execute(query).fetchone()[0]


@pytest.fixture(params=["json", "postgres"])
def histories(request, tmp_path):
    if request.param == "json":
        return JsonHistoryRepository(tmp_path / "history.json")
    from app.history.postgres_repository import PostgresHistoryRepository

    database = request.getfixturevalue("database")
    add_accounts(database, "alice", "bob")
    return PostgresHistoryRepository(database, zone=lambda: LIMA)


@pytest.fixture(params=["json", "postgres"])
def query_logs(request, tmp_path):
    """-> factory(capacity) of QueryLogRepository adapters over one store."""
    if request.param == "json":
        return lambda capacity: JsonQueryLogRepository(tmp_path / "queries.json", capacity)
    from app.metrics.postgres_repository import PostgresQueryLogRepository

    database = request.getfixturevalue("database")
    return lambda capacity: PostgresQueryLogRepository(database, capacity)


def record(domain="shop.pe", verdict="legitima", ts="2026-10-31T09:30", id="h-1",
           source="modelo", score=0.1, latency=40):
    return HistoryRecord(id, domain, verdict, "advertencia", score, source, ts, latency)


def shown(records):
    """Everything but the id, which each adapter is free to assign."""
    return [{name: value for name, value in item.public().items() if name != "id"}
            for item in records]


# History port

def test_history_is_per_user_newest_first(histories):
    assert histories.list("alice") == ()
    first = record(source="cache", score=0.97, latency=3)
    second = record(domain="other.pe", verdict="phishing", ts="2026-10-31T09:45", id="h-2")
    histories.append("alice", first)
    histories.append("alice", second)
    histories.append("bob", record(domain="bob.pe"))
    listed = histories.list("alice")
    assert shown(listed) == shown([second, first])
    assert all(isinstance(item.id, str) and item.id for item in listed)
    assert len({item.id for item in listed}) == 2
    assert shown(histories.list("bob")) == shown([record(domain="bob.pe")])
    assert {name: shown(items) for name, items in histories.all().items()} == {
        "alice": shown([second, first]), "bob": shown([record(domain="bob.pe")])}


def test_history_shows_the_same_visit_within_a_minute_once(histories):
    histories.append("alice", record())
    histories.append("alice", record(domain="other.pe"))
    histories.append("alice", record(ts="2026-10-31T09:31", source="cache"))
    assert [(item.domain, item.ts, item.source) for item in histories.list("alice")] == [
        ("shop.pe", "2026-10-31T09:31", "cache"), ("other.pe", "2026-10-31T09:30", "modelo")]
    histories.append("alice", record(ts="2026-10-31T09:33"))
    histories.append("alice", record(verdict="phishing", ts="2026-10-31T09:33"))
    assert [(item.verdict, item.ts) for item in histories.list("alice")] == [
        ("phishing", "2026-10-31T09:33"), ("legitima", "2026-10-31T09:33"),
        ("legitima", "2026-10-31T09:31"), ("legitima", "2026-10-31T09:30")]


def test_history_shows_the_newest_capacity_records(histories):
    for index in range(CAPACITY + 5):
        histories.append("alice", record(domain=f"d{index}.pe"))
    listed = histories.list("alice")
    assert len(listed) == CAPACITY
    assert listed[0].domain == f"d{CAPACITY + 4}.pe" and listed[-1].domain == "d5.pe"


def test_clearing_a_history_empties_it_for_that_user_only(histories):
    histories.append("alice", record())
    histories.append("bob", record(domain="bob.pe"))
    histories.clear("alice")
    histories.clear("alice")
    assert histories.list("alice") == ()
    assert len(histories.list("bob")) == 1
    histories.append("alice", record(domain="again.pe"))
    assert [item.domain for item in histories.list("alice")] == ["again.pe"]


def test_invalid_history_input_is_a_storage_error(histories):
    for username in ("", None):
        with pytest.raises(HistoryStorageError):
            histories.list(username)
        with pytest.raises(HistoryStorageError):
            histories.clear(username)
        with pytest.raises(HistoryStorageError):
            histories.append(username, record())
    with pytest.raises(HistoryStorageError):
        histories.append("alice", {"domain": "shop.pe"})
    assert histories.list("alice") == ()


# Query-log port

def event(minutes, domain="shop.pe", verdict="legitima", policy="advertencia", source="modelo",
          latency=40):
    return QueryEvent(domain, verdict, policy, source, latency,
                      START + timedelta(minutes=minutes))


def test_query_log_lists_events_oldest_first(query_logs):
    log = query_logs(100)
    assert log.list() == ()
    events = [event(0), event(1, "bad.net", "phishing", "bloqueo", "cache", 3), event(1)]
    for item in events:
        log.append(item)
    assert list(log.list()) == events
    assert all(item.at.tzinfo is not None for item in log.list())


def test_query_log_keeps_the_newest_capacity_events(query_logs):
    log = query_logs(3)
    for minute in range(5):
        log.append(event(minute, f"d{minute}.pe"))
    assert [item.domain for item in log.list()] == ["d2.pe", "d3.pe", "d4.pe"]


def test_invalid_query_event_is_a_storage_error(query_logs):
    log = query_logs(100)
    with pytest.raises(QueryLogStorageError):
        log.append({"domain": "shop.pe"})
    assert log.list() == ()


# PostgreSQL: one consulta row per served verdict

@pytest.fixture
def served(database):
    """-> (verdict service journaling into PostgreSQL, history, metrics, clock, spies)."""
    from app.history.postgres_repository import PostgresHistoryRepository
    from app.metrics.postgres_repository import PostgresQueryLogRepository
    from app.verdict.postgres_journal import PostgresVerdictJournal

    add_accounts(database, "alice", "bob")
    clock = Clock()
    history = HistoryService(PostgresHistoryRepository(database, zone=lambda: clock().tzinfo),
                             clock)
    metrics = MetricsService(PostgresQueryLogRepository(database), clock)
    spies = SpySideEffect(), SpySideEffect()
    service = VerdictService(CacheService(MemoryCacheRepository(), clock), SpyClassifier(),
                             spies[0], query_log=spies[1],
                             journal=PostgresVerdictJournal(database, clock))
    return service, history, metrics, clock, spies


def test_one_row_per_verdict_backs_history_and_metrics(database, served):
    service, history, metrics, clock, spies = served
    url = "https://www.Shop.pe/secret-path?token=abc"
    service.evaluate(url, None)
    clock.advance(minutes=5)
    hit = service.evaluate(url, "alice")
    clock.advance(minutes=5)
    miss = service.evaluate("https://shop.pe/other", "alice")

    assert count(database, "SELECT count(*) FROM consulta") == 3
    assert spies[0].records == [] and spies[1].records == []
    entries = history.list("alice")
    assert [(entry["domain"], entry["source"], entry["ts"]) for entry in entries] == [
        ("shop.pe", "modelo", "2026-10-31T09:40"), ("shop.pe", "cache", "2026-10-31T09:35")]
    for entry, answer in zip(entries, (miss, hit)):
        assert {name: entry[name] for name in answer} == answer
        assert set(entry) == set(answer) | {"id", "ts"}
    assert history.list("bob") == []
    summary = metrics.metrics()
    assert summary["analyzed"] == 3 and summary["phishingCount"] == 3
    assert summary["cacheHitPct"] == 33
    assert [query["source"] for query in summary["queries"]] == ["Modelo", "Caché", "Modelo"]
    assert summary["queries"][0]["stamp"] == "31/10/2026 09:40"


def test_a_miss_records_the_prediction_and_registers_the_model_version(database, served):
    service, _, _, clock, _ = served
    url = "https://www.Shop.pe/secret-path?token=abc"
    service.evaluate(url, None)
    service.evaluate(url, "alice")
    service.evaluate("https://shop.pe/other", "alice")
    with database.transaction(RuntimeError) as connection:
        assert connection.execute(
            "SELECT c.origen, p.veredicto, p.puntaje, p.codigo_version FROM consulta c "
            "LEFT JOIN prediccion p USING (id_consulta) ORDER BY id_consulta").fetchall() == [
                ("MISS", "phishing", 0.81, "9.9"), ("HIT", None, None, None),
                ("MISS", "phishing", 0.81, "9.9")]
        assert connection.execute("SELECT codigo, reporte_metricas FROM version_modelo"
                                  ).fetchall() == [("9.9", None)]
        assert connection.execute(
            "SELECT hash_url, dominio FROM url_evaluada ORDER BY id_url").fetchall() == [
                (cache_key(normalize(url)), "shop.pe"),
                (cache_key(normalize("https://shop.pe/other")), "shop.pe")]
        stored = repr([connection.execute(f"SELECT * FROM {table}").fetchall() for table in (
            "url_evaluada", "consulta", "prediccion", "version_modelo")])
    assert "secret-path" not in stored and "token=abc" not in stored


def test_history_shows_a_repeated_visit_once_while_metrics_count_both(database, served):
    service, history, metrics, clock, _ = served
    service.evaluate("https://shop.pe/a", "alice")
    clock.advance(seconds=30)
    service.evaluate("https://shop.pe/a", "alice")
    entries = history.list("alice")
    assert [(entry["source"], entry["ts"]) for entry in entries] == [
        ("cache", "2026-10-31T09:30")]
    assert metrics.metrics()["analyzed"] == 2
    clock.advance(minutes=2)
    service.evaluate("https://shop.pe/a", "alice")
    assert len(history.list("alice")) == 2


def test_clearing_a_history_unlinks_the_user_and_keeps_the_metrics(database, served):
    service, history, metrics, _, _ = served
    service.evaluate("https://shop.pe/a", "alice")
    service.evaluate("https://shop.pe/b", "bob")
    history.clear("alice")
    assert history.list("alice") == []
    assert len(history.list("bob")) == 1
    assert metrics.metrics()["analyzed"] == 2
    assert count(database, "SELECT count(*) FROM consulta WHERE id_usuario IS NULL") == 1
    assert count(database, "SELECT count(*) FROM prediccion") == 2


def test_a_journal_failure_never_fails_the_verdict(database):
    from app.verdict.postgres_journal import PostgresVerdictJournal

    class Down:
        def transaction(self, error):
            raise error("Storage unavailable")

    service = VerdictService(CacheService(MemoryCacheRepository(), Clock()), SpyClassifier(),
                             SpySideEffect(), journal=PostgresVerdictJournal(Down(), Clock()))
    assert service.evaluate("https://shop.pe/a", "alice")["verdict"] == "phishing"


def test_postgres_history_requires_an_existing_account(database):
    from app.history.postgres_repository import PostgresHistoryRepository

    histories = PostgresHistoryRepository(database, zone=lambda: LIMA)
    with pytest.raises(HistoryStorageError):
        histories.append("ghost", record())
    assert histories.list("ghost") == ()
    assert count(database, "SELECT count(*) FROM consulta") == 0


def test_records_appended_through_the_ports_have_no_url_hash_or_prediction(database):
    """The ports carry a domain only: such rows hang from a hashless url_evaluada row."""
    from app.history.postgres_repository import PostgresHistoryRepository
    from app.metrics.postgres_repository import PostgresQueryLogRepository

    add_accounts(database, "alice")
    PostgresHistoryRepository(database, zone=lambda: LIMA).append("alice", record())
    PostgresQueryLogRepository(database).append(event(0))
    PostgresQueryLogRepository(database).append(event(1, "other.pe"))
    with database.transaction(RuntimeError) as connection:
        assert connection.execute(
            "SELECT hash_url, dominio FROM url_evaluada ORDER BY dominio").fetchall() == [
                (None, "other.pe"), (None, "shop.pe")]
    assert count(database, "SELECT count(*) FROM consulta") == 3
    assert count(database, "SELECT count(*) FROM prediccion") == 0
