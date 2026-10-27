from flask import Blueprint, jsonify, request

from .auth.domain import DuplicateUser, InvalidCredentials, PasswordVerificationError, StorageError


COOKIE_NAME = "sereno_session"


def auth_routes(service):
    routes = Blueprint("auth", __name__, url_prefix="/v1/auth")

    def authenticate(action, status):
        try:
            user, token = action(request.get_json(silent=True))
        except InvalidCredentials:
            return jsonify(error="invalid"), 400 if status == 201 else 401
        response = jsonify(user=user, token=token)
        response.status_code = status
        response.set_cookie(COOKIE_NAME, token, secure=True, httponly=True, samesite="None")
        return response

    @routes.post("/register")
    def register():
        return authenticate(service.register, 201)

    @routes.post("/login")
    def login():
        return authenticate(service.login, 200)

    @routes.get("/session")
    def session():
        return jsonify(session=service.session(request.cookies.get(COOKIE_NAME)))

    @routes.post("/logout")
    def logout():
        if not isinstance(request.get_json(silent=True), dict):
            return jsonify(error="invalid"), 400
        service.logout(request.cookies.get(COOKIE_NAME))
        response = jsonify({})
        response.delete_cookie(COOKIE_NAME, secure=True, httponly=True, samesite="None")
        return response

    @routes.errorhandler(DuplicateUser)
    def duplicate(error):
        return jsonify(error="duplicate"), 409

    @routes.errorhandler(StorageError)
    @routes.errorhandler(PasswordVerificationError)
    def unavailable(error):
        return jsonify(error="server"), 500

    return routes
