"""The `consulta` model: one row per served verdict, written by the verdict
journal, backs both the per-user history and the query log of the metrics."""
from datetime import datetime, timedelta, timezone

import pytest

from app.cache.redis_repository import RedisCacheRepository
from app.cache.service import CacheService
from app.history.domain import CAPACITY, HistoryStorageError
from app.history.postgres_repository import PostgresHistoryRepository
from app.history.service import HistoryService
from app.metrics.domain import QueryEvent
from app.metrics.postgres_repository import PostgresQueryLogRepository
from app.metrics.service import MetricsService
from app.verdict.classifier import Classification
from app.verdict.journal import ServedVerdict
from app.verdict.postgres_journal import PostgresVerdictJournal
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


def count(database, query):
    with database.transaction(RuntimeError) as connection:
        return connection.execute(query).fetchone()[0]


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def journal(database, clock):
    """The journal over a database that has the accounts alice and bob."""
    with database.transaction(RuntimeError) as connection:
        for name in ("alice", "bob"):
            connection.execute(
                "INSERT INTO usuario (nombre, hash_contrasena, rol, fecha_registro) "
                "VALUES (%s, 'hash', 'user', now())", (name,))
    return PostgresVerdictJournal(database, clock)


@pytest.fixture
def histories(database):
    return PostgresHistoryRepository(database, zone=lambda: LIMA)


def serve(journal, username, domain="shop.pe", verdict="legitima", source="modelo", score=0.1,
          latency=40, policy="advertencia"):
    """Records one served verdict of a URL of the domain, as the verdict service does."""
    journal.record(username, ServedVerdict(
        urlHash=cache_key(f"https://{domain}/"), domain=domain, verdict=verdict, policy=policy,
        score=score, source=source, latencyMs=latency,
        modelVersion="9.9" if source == "modelo" else None))


# History port

def test_history_is_per_user_newest_first(journal, histories, clock):
    assert histories.list("alice") == ()
    serve(journal, "alice", source="cache", score=0.97, latency=3)
    clock.advance(minutes=15)
    serve(journal, "alice", domain="other.pe", verdict="phishing")
    serve(journal, "bob", domain="bob.pe")
    serve(journal, None, domain="anonymous.pe")
    assert [item.public() for item in histories.list("alice")] == [
        {"id": "h-2", "domain": "other.pe", "verdict": "phishing", "policy": "advertencia",
         "score": 0.1, "source": "modelo", "ts": "2026-10-31T09:45", "latencyMs": 40},
        {"id": "h-1", "domain": "shop.pe", "verdict": "legitima", "policy": "advertencia",
         "score": 0.97, "source": "cache", "ts": "2026-10-31T09:30", "latencyMs": 3}]
    assert [item.domain for item in histories.list("bob")] == ["bob.pe"]


def test_history_shows_the_same_visit_within_a_minute_once(journal, histories, clock):
    serve(journal, "alice")
    serve(journal, "alice", domain="other.pe")
    clock.advance(minutes=1)
    serve(journal, "alice", source="cache")
    assert [(item.domain, item.ts, item.source) for item in histories.list("alice")] == [
        ("shop.pe", "2026-10-31T09:31", "cache"), ("other.pe", "2026-10-31T09:30", "modelo")]
    clock.advance(minutes=2)
    serve(journal, "alice")
    serve(journal, "alice", verdict="phishing")
    assert [(item.verdict, item.ts) for item in histories.list("alice")] == [
        ("phishing", "2026-10-31T09:33"), ("legitima", "2026-10-31T09:33"),
        ("legitima", "2026-10-31T09:31"), ("legitima", "2026-10-31T09:30")]


def test_history_shows_the_newest_capacity_records(journal, histories):
    for index in range(CAPACITY + 5):
        serve(journal, "alice", domain=f"d{index}.pe")
    listed = histories.list("alice")
    assert len(listed) == CAPACITY
    assert listed[0].domain == f"d{CAPACITY + 4}.pe" and listed[-1].domain == "d5.pe"


def test_clearing_a_history_empties_it_for_that_user_only(journal, histories):
    serve(journal, "alice")
    serve(journal, "bob", domain="bob.pe")
    histories.clear("alice")
    histories.clear("alice")
    assert histories.list("alice") == ()
    assert len(histories.list("bob")) == 1
    serve(journal, "alice", domain="again.pe")
    assert [item.domain for item in histories.list("alice")] == ["again.pe"]


@pytest.mark.parametrize("username", ["", None])
def test_invalid_history_input_is_a_storage_error(histories, username):
    with pytest.raises(HistoryStorageError):
        histories.list(username)
    with pytest.raises(HistoryStorageError):
        histories.clear(username)


def test_a_verdict_of_an_unknown_account_is_kept_without_a_user(database, journal, histories):
    serve(journal, "ghost")
    assert histories.list("ghost") == ()
    assert count(database, "SELECT count(*) FROM consulta WHERE id_usuario IS NULL") == 1


# Query-log port

def test_query_log_lists_events_oldest_first(database, journal, clock):
    log = PostgresQueryLogRepository(database)
    assert log.list() == ()
    serve(journal, "alice")
    clock.advance(minutes=1)
    serve(journal, None, domain="bad.net", verdict="phishing", source="cache", latency=3,
          policy="bloqueo")
    serve(journal, "bob")
    assert list(log.list()) == [
        QueryEvent("shop.pe", "legitima", "advertencia", "modelo", 40, START),
        QueryEvent("bad.net", "phishing", "bloqueo", "cache", 3, START + timedelta(minutes=1)),
        QueryEvent("shop.pe", "legitima", "advertencia", "modelo", 40,
                   START + timedelta(minutes=1))]
    assert all(item.at.tzinfo is not None for item in log.list())


def test_query_log_is_the_newest_capacity_events(database, journal):
    for index in range(5):
        serve(journal, None, domain=f"d{index}.pe")
    assert [item.domain for item in PostgresQueryLogRepository(database, 3).list()] == [
        "d2.pe", "d3.pe", "d4.pe"]
    assert count(database, "SELECT count(*) FROM consulta") == 5


# The verdict service on the journal

@pytest.fixture
def served(database, redis_client, journal, clock):
    """-> (verdict service journaling into PostgreSQL, history, metrics)."""
    history = HistoryService(PostgresHistoryRepository(database, zone=lambda: clock().tzinfo))
    metrics = MetricsService(PostgresQueryLogRepository(database), clock)
    cache = CacheService(RedisCacheRepository(redis_client, database), clock)
    return VerdictService(cache, SpyClassifier(), journal), history, metrics


def test_one_row_per_verdict_backs_history_and_metrics(database, served, clock):
    service, history, metrics = served
    url = "https://www.Shop.pe/secret-path?token=abc"
    service.evaluate(url, None)
    clock.advance(minutes=5)
    hit = service.evaluate(url, "alice")
    clock.advance(minutes=5)
    miss = service.evaluate("https://shop.pe/other", "alice")

    assert count(database, "SELECT count(*) FROM consulta") == 3
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
    service, _, _ = served
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


def test_history_shows_a_repeated_visit_once_while_metrics_count_both(served, clock):
    service, history, metrics = served
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
    service, history, metrics = served
    service.evaluate("https://shop.pe/a", "alice")
    service.evaluate("https://shop.pe/b", "bob")
    history.clear("alice")
    assert history.list("alice") == []
    assert len(history.list("bob")) == 1
    assert metrics.metrics()["analyzed"] == 2
    assert count(database, "SELECT count(*) FROM consulta WHERE id_usuario IS NULL") == 1
    assert count(database, "SELECT count(*) FROM prediccion") == 2


def test_a_journal_failure_never_fails_the_verdict(database, redis_client):
    class Down:
        def transaction(self, error):
            raise error("Storage unavailable")

    cache = CacheService(RedisCacheRepository(redis_client, database), Clock())
    service = VerdictService(cache, SpyClassifier(), PostgresVerdictJournal(Down(), Clock()))
    assert service.evaluate("https://shop.pe/a", "alice")["verdict"] == "phishing"
