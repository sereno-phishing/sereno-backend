import secrets
import string
from datetime import datetime

from .domain import STAMP_FORMAT, HistoryRecord
from .repository import HistoryRepository


_BASE36 = string.digits + string.ascii_lowercase


def local_now():
    return datetime.now().astimezone()


def _base36(number):
    digits = ""
    while True:
        number, remainder = divmod(number, 36)
        digits = _BASE36[remainder] + digits
        if number == 0:
            return digits


class HistoryService:
    """Stamps use the server's local time ("YYYY-MM-DDTHH:MM"), like the
    extension's Sereno.time.stamp."""

    def __init__(self, repository: HistoryRepository, clock=local_now, id_factory=None):
        self.repository = repository
        self.clock = clock
        self.id_factory = id_factory or self.new_id

    def new_id(self):
        millis = int(self.clock().timestamp() * 1000)
        suffix = "".join(secrets.choice(_BASE36) for _ in range(5))
        return "h-" + _base36(millis) + suffix

    def record(self, username, verdict):
        record = HistoryRecord(self.id_factory(), verdict["domain"], verdict["status"],
                               verdict["score"], verdict["source"],
                               self.clock().strftime(STAMP_FORMAT), verdict["latencyMs"])
        self.repository.append(username, record)

    def list(self, username):
        return [record.public() for record in self.repository.list(username)]

    def clear(self, username):
        self.repository.clear(username)
