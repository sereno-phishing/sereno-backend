from datetime import datetime

from .domain import QueryEvent, compute_metrics
from .repository import QueryLogRepository


def local_now():
    return datetime.now().astimezone()


class MetricsService:
    def __init__(self, repository: QueryLogRepository, clock=local_now):
        self.repository = repository
        self.clock = clock

    def record(self, verdict):
        """Logs one served verdict ({domain, verdict, policy, source, latencyMs})."""
        self.repository.append(QueryEvent(verdict["domain"], verdict["verdict"],
                                          verdict["policy"], verdict["source"],
                                          verdict["latencyMs"], self.clock()))

    def metrics(self):
        return compute_metrics(self.repository.list(), self.clock())
