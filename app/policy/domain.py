import re
from dataclasses import dataclass
from datetime import datetime


POLICIES = frozenset({"advertencia", "bloqueo"})
DEFAULT_POLICY = "advertencia"
_DOMAIN_LABEL = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\Z")


class InvalidPolicy(Exception):
    pass


class PolicyStorageError(Exception):
    pass


@dataclass(frozen=True)
class PolicyRow:
    domain: str
    policy: str
    updatedAt: str

    @classmethod
    def create(cls, domain, policy, updated_at, default_policy=False):
        if not isinstance(domain, str) or not isinstance(updated_at, str):
            raise InvalidPolicy
        normalized = domain.strip().lower()
        if (not normalized or len(normalized) > 253
                or any(not _DOMAIN_LABEL.fullmatch(label) for label in normalized.split("."))):
            raise InvalidPolicy
        selected = DEFAULT_POLICY if default_policy and policy is None else policy
        if not isinstance(selected, str) or selected not in POLICIES:
            raise InvalidPolicy
        try:
            parsed = datetime.strptime(updated_at, "%Y-%m-%d")
        except ValueError as error:
            raise InvalidPolicy from error
        if parsed.strftime("%Y-%m-%d") != updated_at:
            raise InvalidPolicy
        return cls(normalized, selected, updated_at)

    def public(self):
        return {"domain": self.domain, "policy": self.policy, "updatedAt": self.updatedAt}
