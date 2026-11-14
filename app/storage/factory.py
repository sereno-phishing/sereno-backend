"""Chooses the persistence adapters of every module from STORAGE_DRIVER. The
composition root (app/__init__.py) wires what this returns into the services;
nothing else knows which driver is in use."""
from dataclasses import dataclass
from typing import Any

from ..auth.json_repository import JsonAuthRepository
from ..cache.json_repository import JsonCacheRepository
from ..config import (cache_data_path, data_path, history_data_path, policy_data_path,
                      query_log_data_path, survey_data_path)
from ..history.json_repository import JsonHistoryRepository
from ..metrics.json_repository import JsonQueryLogRepository
from ..policy.json_repository import JsonPolicyRepository
from ..survey.json_repository import JsonSurveyRepository


@dataclass(frozen=True)
class Repositories:
    auth: Any
    survey: Any
    policy: Any
    cache: Any
    history: Any
    query_log: Any
    # Optional VerdictJournal: one record per served verdict instead of the
    # separate history and query-log writes.
    journal: Any = None


def json_repositories(config):
    """Single-process JSON files: the default, self-contained driver."""
    auth_file = config["DATA_FILE"]
    return Repositories(
        auth=JsonAuthRepository(data_path(auth_file)),
        survey=JsonSurveyRepository(survey_data_path(auth_file, config.get("SURVEY_DATA_FILE"))),
        policy=JsonPolicyRepository(policy_data_path(auth_file, config.get("POLICY_DATA_FILE"))),
        cache=JsonCacheRepository(cache_data_path(auth_file, config.get("CACHE_DATA_FILE"))),
        history=JsonHistoryRepository(
            history_data_path(auth_file, config.get("HISTORY_DATA_FILE"))),
        query_log=JsonQueryLogRepository(
            query_log_data_path(auth_file, config.get("QUERY_LOG_DATA_FILE"))))


def postgres_repositories(config, clock=None):
    """PostgreSQL for the relational data and Redis for the cache entries.
    Both must be reachable: the schema is applied and Redis is pinged here,
    so a missing service stops the start instead of failing every request."""
    # Imported here so the JSON driver runs without the database libraries.
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


DRIVERS = {"json": lambda config, clock=None: json_repositories(config),
           "postgres": postgres_repositories}


def build_repositories(config, clock=None):
    """clock: the optional () -> aware datetime the application was created with."""
    try:
        build = DRIVERS[config["STORAGE_DRIVER"]]
    except (KeyError, TypeError):
        raise ValueError("Unsupported storage driver") from None
    return build(config, clock)
