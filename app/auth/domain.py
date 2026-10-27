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


@dataclass(frozen=True)
class User:
    username: str
    password_hash: str
    role: str
    createdAt: str

    def public(self):
        return {"username": self.username, "role": self.role, "createdAt": self.createdAt}


@dataclass(frozen=True)
class Session:
    username: str
    loginAt: str
    token: str
