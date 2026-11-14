from datetime import datetime

from .domain import compute_metrics
from .repository import QueryLogRepository


def local_now():
    return datetime.now().astimezone()


class MetricsService:
    def __init__(self, repository: QueryLogRepository, clock=local_now):
        self.repository = repository
        self.clock = clock

    def metrics(self):
        return compute_metrics(self.repository.list(), self.clock())
