"""Fixtures for the PostgreSQL and Redis adapters.

They never touch development data: PostgreSQL tests run in a dedicated
database whose name must end in "_test" (created on demand, emptied before
every test) and Redis tests in a non-zero database index (flushed before and
after every test). When a service is unreachable the tests that need it are
skipped, so the suite stays green without Docker."""
import os

import pytest


TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL", "postgresql://sereno:sereno@127.0.0.1:5432/sereno_test")
TEST_REDIS_URL = os.environ.get("TEST_REDIS_URL", "redis://127.0.0.1:6379/15")
START_HINT = "start the services with 'docker compose up -d --wait'"


@pytest.fixture(scope="session")
def postgres_url():
    """The test database URL, after creating that database if it is missing."""
    psycopg = pytest.importorskip("psycopg")
    from psycopg import sql
    from psycopg.conninfo import conninfo_to_dict

    name = conninfo_to_dict(TEST_DATABASE_URL).get("dbname", "")
    if not name.endswith("_test"):
        raise RuntimeError("TEST_DATABASE_URL must name a database ending in '_test'")
    try:
        with psycopg.connect(TEST_DATABASE_URL, dbname="postgres", autocommit=True,
                             connect_timeout=2) as connection:
            exists = connection.execute(
                "SELECT 1 FROM pg_database WHERE datname = %s", (name,)).fetchone()
            if not exists:
                connection.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    except psycopg.OperationalError:
        pytest.skip(f"PostgreSQL is unreachable; {START_HINT}")
    return TEST_DATABASE_URL


@pytest.fixture(scope="session")
def postgres_schema(postgres_url):
    from app.storage.postgres import Database

    database = Database.shared(postgres_url)
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
    redis = pytest.importorskip("redis")
    client = redis.Redis.from_url(TEST_REDIS_URL, socket_connect_timeout=2, socket_timeout=2)
    if client.connection_pool.connection_kwargs.get("db", 0) == 0:
        raise RuntimeError("TEST_REDIS_URL must select a non-zero database index")
    try:
        client.flushdb()
    except redis.RedisError:
        pytest.skip(f"Redis is unreachable; {START_HINT}")
    yield TEST_REDIS_URL
    client.flushdb()
    client.close()


@pytest.fixture
def redis_client(redis_url):
    import redis

    client = redis.Redis.from_url(redis_url, decode_responses=True)
    yield client
    client.close()
