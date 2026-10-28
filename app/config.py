import os
from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parent.parent


def settings():
    return {"STORAGE_DRIVER": os.environ.get("STORAGE_DRIVER", "json"),
            "DATA_FILE": os.environ.get("DATA_FILE", "data/db.json"),
            "AUTH_LOCAL_HTTP": os.environ.get("AUTH_LOCAL_HTTP") == "1"}


def data_path(value):
    path = Path(value)
    return path if path.is_absolute() else BACKEND_ROOT / path
