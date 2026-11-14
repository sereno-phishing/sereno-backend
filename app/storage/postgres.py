"""PostgreSQL access shared by the database adapters: a thread-safe connection
pool, one transaction per repository operation and the idempotent schema."""
from contextlib import contextmanager
from pathlib import Path
from threading import Lock

import psycopg
from psycopg_pool import ConnectionPool


SCHEMA_FILE = Path(__file__).with_name("schema.sql")
# Serializes concurrent schema runs (two processes starting at once).
SCHEMA_LOCK_KEY = 5373726  # arbitrary application-wide constant

_shared = {}
_shared_guard = Lock()


class Database:
    """Safe to share between threads: every operation borrows its own
    connection from the pool for the length of one transaction."""

    def __init__(self, url, max_size=8, timeout=3.0):
        """timeout: seconds to wait for a connection before the operation fails."""
        self.url = url
        self.pool = ConnectionPool(
            url, min_size=1, max_size=max_size, timeout=timeout, open=False,
            kwargs={"connect_timeout": max(1, round(timeout))},
            # A connection broken while idle (server restart) is replaced
            # instead of being handed to a request.
            check=ConnectionPool.check_connection)
        self.pool.open()

    @classmethod
    def shared(cls, url):
        """The process-wide Database of a URL, so that every application
        instance built for the same database uses a single pool."""
        with _shared_guard:
            database = _shared.get(url)
            if database is None or database.pool.closed:
                database = _shared[url] = cls(url)
            return database

    @contextmanager
    def transaction(self, error):
        """-> connection inside one transaction: committed when the block
        ends, rolled back when it raises. Any driver failure (unreachable
        server, constraint, pool exhausted) is raised as error("Storage
        unavailable"), the storage exception of the calling module."""
        try:
            with self.pool.connection() as connection:
                yield connection
        except psycopg.Error as failure:
            raise error("Storage unavailable") from failure

    def apply_schema(self):
        """Creates whatever the schema file defines and is still missing."""
        with self.transaction(DatabaseUnavailable) as connection:
            connection.execute("SELECT pg_advisory_xact_lock(%s)", (SCHEMA_LOCK_KEY,))
            connection.execute(SCHEMA_FILE.read_text(encoding="utf-8"))

    def close(self):
        self.pool.close()


class DatabaseUnavailable(Exception):
    """PostgreSQL could not be reached or prepared at startup."""
