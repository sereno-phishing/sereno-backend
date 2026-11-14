"""Builds the persistence adapters of every module: PostgreSQL for the
relational data and Redis for the cache entries. The composition root
(app/__init__.py) wires what this returns into the services."""
from dataclasses import dataclass
from typing import Any

import redis

from ..auth.postgres_repository import PostgresAuthRepository
from ..cache.domain import CacheStorageError
from ..cache.redis_repository import RedisCacheRepository
from ..history.postgres_repository import PostgresHistoryRepository
from ..metrics.postgres_repository import PostgresQueryLogRepository
from ..policy.postgres_repository import PostgresPolicyRepository
from ..survey.postgres_repository import PostgresSurveyRepository
from ..verdict.postgres_journal import PostgresVerdictJournal
from .postgres import Database, DatabaseUnavailable


@dataclass(frozen=True)
class Repositories:
    auth: Any
    survey: Any
    policy: Any
    cache: Any
    history: Any
    query_log: Any
    # VerdictJournal: one record per served verdict instead of the separate
    # history and query-log writes.
    journal: Any = None


def build_repositories(config, clock=None):
    """clock: the optional () -> aware datetime the application was created with.
    Both services must be reachable: the schema is applied and Redis is pinged
    here, so a missing service stops the start instead of failing every request."""
    database = Database.shared(config["DATABASE_URL"])
    try:
        database.apply_schema()
    except DatabaseUnavailable:
        database.close()
        raise
    client = redis.Redis.from_url(config["REDIS_URL"], decode_responses=True,
                                  socket_connect_timeout=2, socket_timeout=2)
    try:
        client.ping()
    except redis.RedisError as failure:
        raise CacheStorageError("Cache unavailable") from failure
    clocked = [clock] if clock else []
    return Repositories(
        auth=PostgresAuthRepository(database),
        survey=PostgresSurveyRepository(database),
        policy=PostgresPolicyRepository(database),
        cache=RedisCacheRepository(client, database),
        # History stamps are local time: follow the injected clock's zone.
        history=PostgresHistoryRepository(
            database, zone=(lambda: clock().tzinfo) if clock else None),
        query_log=PostgresQueryLogRepository(database),
        journal=PostgresVerdictJournal(database, *clocked))
