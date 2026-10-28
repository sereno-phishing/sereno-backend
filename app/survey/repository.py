from typing import Protocol

from .domain import SurveySubmission


class SurveyRepository(Protocol):
    """Port for anonymous responses and separate account participation."""

    def submit(self, username: str, submission: SurveySubmission) -> None: ...

    def has_submitted(self, username: str) -> bool: ...

    def list_submissions(self) -> tuple[SurveySubmission, ...]: ...
