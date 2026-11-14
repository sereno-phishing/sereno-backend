import os
from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parent.parent
CLASSIFIERS = ("model", "fake")
# The artifact trained in the sibling sereno-model repository.
DEFAULT_MODEL_PATH = "../sereno-model/artifacts/2.0.0/model.joblib"
# The services of docker-compose.yml with their development-only defaults.
DEFAULT_DATABASE_URL = "postgresql://sereno:sereno@127.0.0.1:5432/sereno"
DEFAULT_REDIS_URL = "redis://127.0.0.1:6379/0"


def settings():
    # CLASSIFIER defaults to the fake so that nothing (the test suite first)
    # loads the model artifact unless asked to; start.ps1 asks for the model.
    return {"CLASSIFIER": os.environ.get("CLASSIFIER", "fake"),
            "MODEL_PATH": os.environ.get("MODEL_PATH", DEFAULT_MODEL_PATH),
            # "json" keeps a bare start and the test suite self-contained;
            # "postgres" stores in PostgreSQL and caches verdicts in Redis.
            "STORAGE_DRIVER": os.environ.get("STORAGE_DRIVER", "json"),
            "DATABASE_URL": os.environ.get("DATABASE_URL", DEFAULT_DATABASE_URL),
            "REDIS_URL": os.environ.get("REDIS_URL", DEFAULT_REDIS_URL),
            "DATA_FILE": os.environ.get("DATA_FILE", "data/db.json"),
            "SURVEY_DATA_FILE": os.environ.get("SURVEY_DATA_FILE"),
            "POLICY_DATA_FILE": os.environ.get("POLICY_DATA_FILE"),
            "CACHE_DATA_FILE": os.environ.get("CACHE_DATA_FILE"),
            "HISTORY_DATA_FILE": os.environ.get("HISTORY_DATA_FILE"),
            "QUERY_LOG_DATA_FILE": os.environ.get("QUERY_LOG_DATA_FILE"),
            "AUTH_LOCAL_HTTP": os.environ.get("AUTH_LOCAL_HTTP") == "1",
            # Unauthenticated developer tools under /dev: off unless asked
            # for, and never to be enabled outside a developer machine.
            "DEV_TOOLS": os.environ.get("DEV_TOOLS") == "1"}


def data_path(value):
    path = Path(value)
    return path if path.is_absolute() else BACKEND_ROOT / path


def model_path(value):
    """MODEL_PATH resolved against the backend root, with ".." collapsed."""
    return Path(os.path.normpath(data_path(value)))


def adjacent_data_path(auth_value, configured_value, filename):
    if configured_value:
        return data_path(configured_value)
    return data_path(auth_value).resolve().with_name(filename)


def survey_data_path(auth_value, survey_value=None):
    return adjacent_data_path(auth_value, survey_value, "surveys.json")


def policy_data_path(auth_value, policy_value=None):
    return adjacent_data_path(auth_value, policy_value, "policies.json")


def cache_data_path(auth_value, cache_value=None):
    return adjacent_data_path(auth_value, cache_value, "cache.json")


def history_data_path(auth_value, history_value=None):
    return adjacent_data_path(auth_value, history_value, "history.json")


def query_log_data_path(auth_value, query_log_value=None):
    return adjacent_data_path(auth_value, query_log_value, "queries.json")
