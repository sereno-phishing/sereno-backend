from ..auth.domain import ROLES
from ..auth.repository import AuthRepository


class UnknownUser(Exception):
    pass


class InvalidRole(Exception):
    pass


def _view(user):
    """What the tool shows of an account: never the password hash."""
    return {"username": user.username, "role": user.role, "createdAt": user.createdAt}


class DevUserService:
    """Account administration for developers: list, change a role, delete.
    It asks for no session, so it is only ever wired when DEV_TOOLS is on.
    Deleting relies on the repository removing or unlinking everything that
    hangs from the account in one atomic step."""

    def __init__(self, repository: AuthRepository):
        self.repository = repository

    def list(self):
        return [_view(user) for user in self.repository.list_users()]

    def set_role(self, username, role):
        if not isinstance(role, str) or role not in ROLES:
            raise InvalidRole
        if not self.repository.set_role(username, role):
            raise UnknownUser
        user = self.repository.get_user(username)
        if user is None:
            raise UnknownUser
        return _view(user)

    def delete(self, username):
        if not self.repository.delete_user(username):
            raise UnknownUser
