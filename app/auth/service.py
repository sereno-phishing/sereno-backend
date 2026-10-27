from datetime import datetime, timezone
from secrets import token_urlsafe
from typing import Callable

from .domain import Credentials, DuplicateUser, InvalidCredentials, PasswordVerificationError, Session, User
from .repository import AuthRepository


def timestamp():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M")


class AuthService:
    def __init__(self, repository: AuthRepository, hash_password: Callable,
                 verify_password: Callable, clock=timestamp, token_factory=token_urlsafe):
        self.repository = repository
        self.hash_password = hash_password
        self.verify_password = verify_password
        self.clock = clock
        self.token_factory = token_factory

    def register(self, credentials: Credentials):
        username, password = credentials.username, credentials.password
        user = User(username, self.hash_password(password), "usuario", self.clock())
        session = Session(username, self.clock(), self.token_factory())
        if not self.repository.register(user, session):
            raise DuplicateUser
        return user.public(), session.token

    def login(self, credentials: Credentials):
        username, password = credentials.username, credentials.password
        user = self.repository.get_user(username)
        if user is None:
            raise InvalidCredentials
        try:
            verified = self.verify_password(user.password_hash, password)
        except ValueError as error:
            # The injected verifier rejects an unusable persisted hash.
            raise PasswordVerificationError from error
        if not verified:
            raise InvalidCredentials
        session = Session(username, self.clock(), self.token_factory())
        self.repository.add_session(session)
        return user.public(), session.token

    def session(self, token):
        if not token:
            return None
        session = self.repository.get_session(token)
        if session is None:
            return None
        user = self.repository.get_user(session.username)
        if user is None:
            return None
        return {"username": user.username, "role": user.role,
                "loginAt": session.loginAt, "token": session.token}

    def logout(self, token):
        if token:
            self.repository.delete_session(token)
