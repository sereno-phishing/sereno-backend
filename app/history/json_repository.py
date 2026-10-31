from ..json_file import JsonFile
from .domain import CAPACITY, HistoryRecord, HistoryStorageError, append


def _validate(data):
    if not isinstance(data, dict) or set(data) != {"history"}:
        raise ValueError("Invalid schema")
    if not isinstance(data["history"], dict):
        raise ValueError("Invalid collection")
    for username, records in data["history"].items():
        if not isinstance(username, str) or not username or not isinstance(records, list):
            raise ValueError("Invalid user history")
        if len(records) > CAPACITY:
            raise ValueError("Too many records")
        for record in records:
            HistoryRecord.from_public(record)


class JsonHistoryRepository:
    """JSON adapter of HistoryRepository (single-process development storage)."""

    def __init__(self, path):
        self.file = JsonFile(path, {"history": {}}, _validate, HistoryStorageError)

    @staticmethod
    def _valid_username(username):
        if not isinstance(username, str) or not username:
            raise HistoryStorageError("Invalid username")

    def list(self, username):
        self._valid_username(username)
        with self.file.lock:
            records = self.file.read()["history"].get(username, [])
            return tuple(HistoryRecord.from_public(record) for record in records)

    def all(self):
        with self.file.lock:
            return {username: tuple(HistoryRecord.from_public(record) for record in records)
                    for username, records in self.file.read()["history"].items()}

    def append(self, username, record):
        self._valid_username(username)
        if not isinstance(record, HistoryRecord):
            raise HistoryStorageError("Invalid record")

        def change(data):
            current = tuple(HistoryRecord.from_public(item)
                            for item in data["history"].get(username, []))
            data["history"][username] = [item.public() for item in append(current, record)]
            return data, None
        self.file.update(change)

    def clear(self, username):
        self._valid_username(username)

        def change(data):
            data["history"][username] = []
            return data, None
        self.file.update(change)
