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
