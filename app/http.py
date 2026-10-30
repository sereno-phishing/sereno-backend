from ipaddress import ip_address
from urllib.parse import urlsplit

from flask import Blueprint, current_app, jsonify, request

from .auth.domain import Credentials, DuplicateUser, InvalidCredentials, PasswordVerificationError, StorageError
from .policy.domain import InvalidPolicy, PolicyRow, PolicyStorageError
from .survey.domain import AlreadySubmitted, InvalidSurvey, SurveyStorageError


COOKIE_NAME = "sereno_session"


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


def auth_routes(service):
    routes = Blueprint("auth", __name__, url_prefix="/v1/auth")
    routes.before_request(guard_local_http)

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


def policy_routes(auth_service, policy_service):
    routes = Blueprint("policy", __name__, url_prefix="/v1")
    routes.before_request(guard_local_http)

    def authorize():
        session = auth_service.session(request.cookies.get(COOKIE_NAME))
        if session is None:
            return None, (jsonify(error="invalid"), 401)
        if session["isAdmin"] is not True:
            return None, (jsonify(error="forbidden"), 403)
        return session, None

    @routes.get("/domains")
    def list_domains():
        _, denied = authorize()
        if denied:
            return denied
        return jsonify(domains=policy_service.list())

    @routes.put("/domains")
    def replace_domains():
        _, denied = authorize()
        if denied:
            return denied
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict) or set(payload) != {"domains"}:
            return jsonify(error="invalid"), 400
        records = payload["domains"]
        if not isinstance(records, list):
            return jsonify(error="invalid"), 400
        try:
            rows = []
            for record in records:
                if (not isinstance(record, dict)
                        or set(record) != {"domain", "policy", "updatedAt"}):
                    raise InvalidPolicy
                rows.append(PolicyRow.create(
                    record["domain"], record["policy"], record["updatedAt"]))
            domains = policy_service.replace(tuple(rows))
        except InvalidPolicy:
            return jsonify(error="invalid"), 400
        return jsonify(domains=domains)

    @routes.errorhandler(StorageError)
    @routes.errorhandler(PolicyStorageError)
    def unavailable(error):
        return jsonify(error="server"), 500

    return routes


def survey_routes(auth_service, survey_service):
    routes = Blueprint("survey", __name__, url_prefix="/v1")
    routes.before_request(guard_local_http)

    def current_username():
        session = auth_service.session(request.cookies.get(COOKIE_NAME))
        return None if session is None else session["username"]

    @routes.get("/survey")
    def status():
        username = current_username()
        if username is None:
            return jsonify(error="invalid"), 401
        return jsonify(submitted=survey_service.has_submitted(username))

    @routes.post("/survey")
    def submit():
        username = current_username()
        if username is None:
            return jsonify(error="invalid"), 401
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict) or set(payload) != {"answers"}:
            return jsonify(error="invalid"), 400
        try:
            survey_service.submit(username, payload["answers"])
        except InvalidSurvey:
            return jsonify(error="invalid"), 400
        return jsonify({})

    @routes.errorhandler(AlreadySubmitted)
    def already_submitted(error):
        return jsonify(error="already_submitted"), 409

    @routes.errorhandler(StorageError)
    @routes.errorhandler(SurveyStorageError)
    def unavailable(error):
        return jsonify(error="server"), 500

    return routes
