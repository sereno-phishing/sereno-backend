from .repository import HistoryRepository


class HistoryService:
    """A user's history is read from the queries the verdict journal
    recorded for that account; nothing is written here."""

    def __init__(self, repository: HistoryRepository):
        self.repository = repository

    def list(self, username):
        return [record.public() for record in self.repository.list(username)]

    def clear(self, username):
        self.repository.clear(username)
