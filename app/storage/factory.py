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


DRIVERS = {"json": json_repositories}


def build_repositories(config):
    try:
        build = DRIVERS[config["STORAGE_DRIVER"]]
    except (KeyError, TypeError):
        raise ValueError("Unsupported storage driver") from None
    return build(config)
