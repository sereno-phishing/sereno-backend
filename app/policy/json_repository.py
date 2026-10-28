import json
import os
import tempfile
from pathlib import Path
from threading import Lock, RLock

from .domain import InvalidPolicy, PolicyRow, PolicyStorageError


_locks = {}
_locks_guard = Lock()


class JsonPolicyRepository:
    """Single-process storage; all instances share a lock per resolved path."""

    def __init__(self, path):
        self.path = Path(path).resolve()
        with _locks_guard:
            self.lock = _locks.setdefault(self.path, RLock())
        with self.lock:
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                if not self.path.exists():
                    self._write({"domains": []})
                self._read()
            except OSError as error:
                raise PolicyStorageError("Storage unavailable") from error

    @staticmethod
    def _row(record):
        if not isinstance(record, dict) or set(record) != {"domain", "policy", "updatedAt"}:
            raise InvalidPolicy
        row = PolicyRow.create(record["domain"], record["policy"], record["updatedAt"])
        if row.public() != record:
            raise InvalidPolicy
        return row

    @classmethod
    def _validate(cls, data):
        if not isinstance(data, dict) or set(data) != {"domains"}:
            raise ValueError("Invalid schema")
        if not isinstance(data["domains"], list):
            raise ValueError("Invalid collection")
        rows = tuple(cls._row(record) for record in data["domains"])
        domains = [row.domain for row in rows]
        if len(domains) != len(set(domains)):
            raise ValueError("Duplicate domain")
        return rows

    def _read(self):
        try:
            with self.path.open(encoding="utf-8") as source:
                data = json.load(source)
            return self._validate(data)
        except (OSError, ValueError, TypeError, InvalidPolicy) as error:
            raise PolicyStorageError("Storage unavailable") from error

    def _write(self, data):
        temporary = None
        try:
            self._validate(data)
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=self.path.parent,
                                             prefix=self.path.name + ".", delete=False) as target:
                temporary = target.name
                json.dump(data, target, ensure_ascii=True, indent=2)
                target.flush()
                os.fsync(target.fileno())
            os.replace(temporary, self.path)
        except (OSError, ValueError, TypeError, InvalidPolicy) as error:
            raise PolicyStorageError("Storage unavailable") from error
        finally:
            if temporary is not None:
                try:
                    Path(temporary).unlink(missing_ok=True)
                except OSError:
                    pass

    def list(self):
        with self.lock:
            return self._read()

    def replace(self, rows):
        if (not isinstance(rows, tuple)
                or any(not isinstance(row, PolicyRow) for row in rows)):
            raise PolicyStorageError("Invalid policies")
        with self.lock:
            self._read()
            self._write({"domains": [row.public() for row in rows]})
