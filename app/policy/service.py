from .domain import DEFAULT_POLICY, InvalidPolicy, PolicyRow
from .repository import PolicyRepository


class PolicyService:
    def __init__(self, repository: PolicyRepository):
        self.repository = repository

    def policy_for(self, domain):
        """The response policy of a verdict domain (no leading "www."): its
        row, else the row typed with "www.", else the default policy."""
        policies = {row.domain: row.policy for row in self.repository.list()}
        return policies.get(domain) or policies.get("www." + domain) or DEFAULT_POLICY

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
