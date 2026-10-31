from datetime import datetime

from ..json_file import JsonFile
from .domain import CAPACITY, InvalidQueryEvent, QueryEvent, QueryLogStorageError


FIELDS = {"domain", "status", "source", "latencyMs", "at"}


def _event(record):
    if not isinstance(record, dict) or set(record) != FIELDS or not isinstance(record["at"], str):
        raise InvalidQueryEvent
    return QueryEvent(record["domain"], record["status"], record["source"], record["latencyMs"],
                      datetime.fromisoformat(record["at"]))


def _record(event):
    return {"domain": event.domain, "status": event.status, "source": event.source,
            "latencyMs": event.latencyMs, "at": event.at.isoformat()}


def _validate(data):
    if not isinstance(data, dict) or set(data) != {"queries"} or not isinstance(data["queries"], list):
        raise ValueError("Invalid schema")
    for record in data["queries"]:
        _event(record)


class JsonQueryLogRepository:
    """JSON adapter of QueryLogRepository keeping the newest `capacity` events."""

    def __init__(self, path, capacity=CAPACITY):
        self.capacity = capacity
        self.file = JsonFile(path, {"queries": []}, _validate, QueryLogStorageError)

    def append(self, event):
        if not isinstance(event, QueryEvent):
            raise QueryLogStorageError("Invalid event")

        def change(data):
            data["queries"] = [*data["queries"], _record(event)][-self.capacity:]
            return data, None
        self.file.update(change)

    def list(self):
        with self.file.lock:
            return tuple(_event(record) for record in self.file.read()["queries"])
