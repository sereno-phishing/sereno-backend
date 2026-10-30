from dataclasses import dataclass


class InvalidCredentials(Exception):
    pass


class DuplicateUser(Exception):
    pass


class StorageError(Exception):
    pass


class PasswordVerificationError(Exception):
    pass


@dataclass(frozen=True)
class Credentials:
    username: str
    password: str

    def __post_init__(self):
        if not isinstance(self.username, str) or not isinstance(self.password, str):
            raise InvalidCredentials
        if not self.username.strip() or not self.password:
            raise InvalidCredentials
        try:
            self.username.encode("utf-8")
            self.password.encode("utf-8")
        except UnicodeEncodeError as error:
            raise InvalidCredentials from error
        object.__setattr__(self, "username", self.username.strip())


USER_ROLE = "user"
ADMIN_ROLE = "admin"
ROLES = frozenset({USER_ROLE, ADMIN_ROLE})


@dataclass(frozen=True)
class User:
    username: str
    password_hash: str
    role: str
    createdAt: str

    @property
    def is_admin(self):
        return self.role == ADMIN_ROLE

    def public(self):
        return {"username": self.username, "isAdmin": self.is_admin, "createdAt": self.createdAt}


@dataclass(frozen=True)
class Session:
    username: str
    loginAt: str
    token: str
