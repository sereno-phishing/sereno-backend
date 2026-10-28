from .domain import InvalidPolicy, PolicyRow
from .repository import PolicyRepository


class PolicyService:
    def __init__(self, repository: PolicyRepository):
        self.repository = repository

    def list(self):
        return tuple(row.public() for row in self.repository.list())

    def replace(self, rows):
        if (not isinstance(rows, tuple)
                or any(not isinstance(row, PolicyRow) for row in rows)):
            raise InvalidPolicy
        domains = [row.domain for row in rows]
        if len(domains) != len(set(domains)):
            raise InvalidPolicy
        self.repository.replace(rows)
        return tuple(row.public() for row in rows)
