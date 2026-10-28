from dataclasses import dataclass
from datetime import datetime


class InvalidSurvey(Exception):
    pass


class SurveyStorageError(Exception):
    pass


class AlreadySubmitted(Exception):
    pass


@dataclass(frozen=True)
class SurveySubmission:
    answers: tuple[int, ...]
    submittedAt: str

    def __post_init__(self):
        if (not isinstance(self.answers, tuple) or len(self.answers) != 10
                or any(type(answer) is not int or not 1 <= answer <= 5
                       for answer in self.answers)):
            raise InvalidSurvey
        if not isinstance(self.submittedAt, str):
            raise InvalidSurvey
        try:
            parsed = datetime.strptime(self.submittedAt, "%Y-%m-%dT%H:%M:%SZ")
        except ValueError as error:
            raise InvalidSurvey from error
        if parsed.strftime("%Y-%m-%dT%H:%M:%SZ") != self.submittedAt:
            raise InvalidSurvey
