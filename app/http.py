from ipaddress import ip_address
from urllib.parse import urlsplit

from flask import Blueprint, current_app, jsonify, request

from .auth.domain import Credentials, DuplicateUser, InvalidCredentials, PasswordVerificationError, StorageError


COOKIE_NAME = "sereno_session"


def auth_routes(service):
    routes = Blueprint("auth", __name__, url_prefix="/v1/auth")

    @routes.before_request
    def guard_local_http():
        if not current_app.config["AUTH_LOCAL_HTTP"]:
            return None
        # Direct loopback only: never trust a proxy's forwarded client or host.
        forwarded = any(name.lower() == "forwarded" or name.lower().startswith("x-forwarded-")
                        for name in request.headers.keys())
        try:
            local = (ip_address(request.remote_addr).is_loopback
                     and ip_address(urlsplit(request.host_url).hostname).is_loopback)
        except (ValueError, TypeError):
            local = False
        if forwarded or not local:
            return jsonify(error="server"), 403
        return None

    def cookie_options():
        local = current_app.config["AUTH_LOCAL_HTTP"]
        return {"secure": not local, "httponly": True, "samesite": "Lax" if local else "None"}

    def authenticate(action, status):
        try:
            payload = request.get_json(silent=True)
            if not isinstance(payload, dict):
                raise InvalidCredentials
            username, password = payload.get("username"), payload.get("password")
            if not isinstance(username, str) or not isinstance(password, str):
                raise InvalidCredentials
            user, token = action(Credentials(username, password))
        except InvalidCredentials:
            return jsonify(error="invalid"), 400 if status == 201 else 401
        response = jsonify(user=user, token=token)
        response.status_code = status
        response.set_cookie(COOKIE_NAME, token, **cookie_options())
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
        response.delete_cookie(COOKIE_NAME, **cookie_options())
        return response

    @routes.errorhandler(DuplicateUser)
    def duplicate(error):
        return jsonify(error="duplicate"), 409

    @routes.errorhandler(StorageError)
    @routes.errorhandler(PasswordVerificationError)
    def unavailable(error):
        return jsonify(error="server"), 500

    return routes
