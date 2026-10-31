"""Locked, atomically replaced JSON document shared by the newer JSON adapters.

Single-process storage: every instance for the same resolved path shares one
lock. Writes go through a temporary sibling, fsync and os.replace. Invalid
existing data fails closed (the validator raises) and is never reset.
"""
import json
import os
import tempfile
from pathlib import Path
from threading import Lock, RLock


_locks = {}
_locks_guard = Lock()


class JsonFile:
    def __init__(self, path, initial, validate, error):
        """validate(data) raises on an invalid document; error is the adapter's
        storage exception class, raised for any read/write/validation failure."""
        self.path = Path(path).resolve()
        self.validate = validate
        self.error = error
        with _locks_guard:
            self.lock = _locks.setdefault(self.path, RLock())
        with self.lock:
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                if not self.path.exists():
                    self.write(initial)
                self.read()
            except OSError as failure:
                raise error("Storage unavailable") from failure

    def read(self):
        try:
            with self.path.open(encoding="utf-8") as source:
                data = json.load(source)
            self.validate(data)
            return data
        except Exception as failure:
            raise self.error("Storage unavailable") from failure

    def write(self, data):
        temporary = None
        try:
            self.validate(data)
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=self.path.parent,
                                             prefix=self.path.name + ".", delete=False) as target:
                temporary = target.name
                json.dump(data, target, ensure_ascii=True, indent=2)
                target.flush()
                os.fsync(target.fileno())
            os.replace(temporary, self.path)
        except Exception as failure:
            raise self.error("Storage unavailable") from failure
        finally:
            if temporary is not None:
                try:
                    Path(temporary).unlink(missing_ok=True)
                except OSError:
                    pass

    def update(self, change):
        """Runs change(data) -> (new_data, result) under the lock and persists new_data."""
        with self.lock:
            data, result = change(self.read())
            self.write(data)
            return result
