from datetime import datetime, timezone
from typing import Callable

from .domain import SurveySubmission
from .repository import SurveyRepository


def timestamp():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class SurveyService:
    def __init__(self, repository: SurveyRepository, clock: Callable = timestamp):
        self.repository = repository
        self.clock = clock

    def submit(self, username, answers):
        submission = SurveySubmission(tuple(answers) if isinstance(answers, list) else answers,
                                      self.clock())
        self.repository.submit(username, submission)

    def has_submitted(self, username):
        return self.repository.has_submitted(username)
