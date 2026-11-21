import os
from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parent.parent
CLASSIFIERS = ("model", "fake")
# The artifact trained in the sibling sereno-model repository.
DEFAULT_MODEL_PATH = "../sereno-model/artifacts/3.0.0/model.joblib"
# The services of docker-compose.yml with their development-only defaults.
DEFAULT_DATABASE_URL = "postgresql://sereno:sereno@127.0.0.1:5432/sereno"
DEFAULT_REDIS_URL = "redis://127.0.0.1:6379/0"


def settings():
    # CLASSIFIER defaults to the fake so that nothing (the test suite first)
    # loads the model artifact unless asked to; start.ps1 asks for the model.
    return {"CLASSIFIER": os.environ.get("CLASSIFIER", "fake"),
            "MODEL_PATH": os.environ.get("MODEL_PATH", DEFAULT_MODEL_PATH),
            # PostgreSQL stores the data and Redis caches the verdicts.
            "DATABASE_URL": os.environ.get("DATABASE_URL", DEFAULT_DATABASE_URL),
            "REDIS_URL": os.environ.get("REDIS_URL", DEFAULT_REDIS_URL),
            "AUTH_LOCAL_HTTP": os.environ.get("AUTH_LOCAL_HTTP") == "1",
            # The verdict cache: on unless exactly "0" (the response-time
            # experiment runs without it; see the README).
            "CACHE_ENABLED": os.environ.get("CACHE_ENABLED", "1") != "0",
            # Unauthenticated developer tools under /dev: off unless asked
            # for, and never to be enabled outside a developer machine.
            "DEV_TOOLS": os.environ.get("DEV_TOOLS") == "1"}


def model_path(value):
    """MODEL_PATH resolved against the backend root, with ".." collapsed."""
    return Path(os.path.normpath(BACKEND_ROOT / value))
