"""Every test runs against PostgreSQL and Redis, the only storage of the
backend. Start them with 'docker compose up -d --wait': without them the run
stops at once with that instruction instead of skipping tests.

Development data is never touched: PostgreSQL tests use a dedicated database
whose name must end in "_test" (created on demand, its schema rebuilt once per
run and its tables emptied before every test) and Redis tests a non-zero
database index (flushed before and after every test)."""
import os

import psycopg
import pytest
import redis
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict
from psycopg_pool import ConnectionPool

from app.storage.postgres import Database


TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL", "postgresql://sereno:sereno@127.0.0.1:5432/sereno_test")
TEST_REDIS_URL = os.environ.get("TEST_REDIS_URL", "redis://127.0.0.1:6379/15")
START_COMMAND = "docker compose up -d --wait"


def unreachable_services(database_url, redis_url):
    """-> the names of the services that do not answer, in a fixed order."""
    missing = []
    try:
        # The server's maintenance database: the test database may not exist yet.
        psycopg.connect(database_url, dbname="postgres", connect_timeout=2).close()
    except psycopg.OperationalError:
        missing.append("PostgreSQL")
    client = redis.Redis.from_url(redis_url, socket_connect_timeout=2, socket_timeout=2)
    try:
        client.ping()
    except redis.RedisError:
        missing.append("Redis")
    finally:
        client.close()
    return missing


def services_down_message(missing):
    return (f"{' and '.join(missing)} unreachable. The tests need both services: run "
            f"'{START_COMMAND}' in the backend folder, then run the tests again.")


def pytest_sessionstart(session):
    missing = unreachable_services(TEST_DATABASE_URL, TEST_REDIS_URL)
    if missing:
        pytest.exit(services_down_message(missing), returncode=1)


@pytest.fixture(scope="session")
def postgres_schema():
    """The test database, created if missing, with the current schema and
    nothing else: whatever an earlier version of the schema left is dropped."""
    name = conninfo_to_dict(TEST_DATABASE_URL).get("dbname", "")
    if not name.endswith("_test"):
        raise RuntimeError("TEST_DATABASE_URL must name a database ending in '_test'")
    with psycopg.connect(TEST_DATABASE_URL, dbname="postgres", autocommit=True,
                         connect_timeout=2) as connection:
        exists = connection.execute(
            "SELECT 1 FROM pg_database WHERE datname = %s", (name,)).fetchone()
        if not exists:
            connection.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    with psycopg.connect(TEST_DATABASE_URL, autocommit=True, connect_timeout=2) as connection:
        connection.execute("DROP SCHEMA public CASCADE")
        connection.execute("CREATE SCHEMA public")
    database = Database.shared(TEST_DATABASE_URL)
    database.apply_schema()
    yield database
    database.close()


@pytest.fixture
def database(postgres_schema):
    """The test database with the schema applied and every table empty."""
    with postgres_schema.transaction(RuntimeError) as connection:
        tables = [row[0] for row in connection.execute(
            "SELECT tablename FROM pg_tables WHERE schemaname = 'public'").fetchall()]
        connection.execute("TRUNCATE {} RESTART IDENTITY CASCADE".format(
            ", ".join('"' + table + '"' for table in tables)))
    return postgres_schema


@pytest.fixture
def redis_url():
    client = redis.Redis.from_url(TEST_REDIS_URL, socket_connect_timeout=2, socket_timeout=2)
    if client.connection_pool.connection_kwargs.get("db", 0) == 0:
        raise RuntimeError("TEST_REDIS_URL must select a non-zero database index")
    client.flushdb()
    yield TEST_REDIS_URL
    client.flushdb()
    client.close()


@pytest.fixture
def redis_client(redis_url):
    client = redis.Redis.from_url(redis_url, decode_responses=True)
    yield client
    client.close()


@pytest.fixture
def config(database, redis_url):
    """create_app() settings for the empty test database and Redis index."""
    return {"TESTING": True, "DATABASE_URL": database.url, "REDIS_URL": redis_url}


@pytest.fixture
def database_down(monkeypatch):
    """-> stop(): from then on no connection to PostgreSQL can be obtained,
    as when the server is unreachable, until the test ends."""
    def stop():
        def refuse(self, *arguments, **keywords):
            raise psycopg.OperationalError("connection refused")
        monkeypatch.setattr(ConnectionPool, "connection", refuse)
    return stop


def rows(config, *tables):
    """Every row of the given tables of the test database, to compare what is
    stored before and after a request."""
    with Database.shared(config["DATABASE_URL"]).transaction(RuntimeError) as connection:
        return [connection.execute(
            sql.SQL("SELECT * FROM {} ORDER BY 1").format(sql.Identifier(table))).fetchall()
            for table in tables]


def set_role(config, username, role):
    """What an operator does by hand: the product API has no promotion route."""
    with Database.shared(config["DATABASE_URL"]).transaction(RuntimeError) as connection:
        updated = connection.execute(
            "UPDATE usuario SET rol = %s WHERE nombre = %s", (role, username)).rowcount
    assert updated == 1
