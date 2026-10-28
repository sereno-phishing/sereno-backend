from typing import Protocol

from .domain import PolicyRow


class PolicyRepository(Protocol):
    def list(self) -> tuple[PolicyRow, ...]: ...

    def replace(self, rows: tuple[PolicyRow, ...]) -> None: ...
