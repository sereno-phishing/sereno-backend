import os
from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parent.parent


def settings():
    return {"STORAGE_DRIVER": os.environ.get("STORAGE_DRIVER", "json"),
            "DATA_FILE": os.environ.get("DATA_FILE", "data/db.json"),
            "SURVEY_DATA_FILE": os.environ.get("SURVEY_DATA_FILE"),
            "POLICY_DATA_FILE": os.environ.get("POLICY_DATA_FILE"),
            "AUTH_LOCAL_HTTP": os.environ.get("AUTH_LOCAL_HTTP") == "1"}


def data_path(value):
    path = Path(value)
    return path if path.is_absolute() else BACKEND_ROOT / path


def adjacent_data_path(auth_value, configured_value, filename):
    if configured_value:
        return data_path(configured_value)
    return data_path(auth_value).resolve().with_name(filename)


def survey_data_path(auth_value, survey_value=None):
    return adjacent_data_path(auth_value, survey_value, "surveys.json")


def policy_data_path(auth_value, policy_value=None):
    return adjacent_data_path(auth_value, policy_value, "policies.json")
