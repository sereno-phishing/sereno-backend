from flask import Flask
from werkzeug.security import check_password_hash, generate_password_hash

from .auth.json_repository import JsonAuthRepository
from .auth.service import AuthService
from .config import data_path, policy_data_path, settings, survey_data_path
from .http import auth_routes, policy_routes, survey_routes
from .policy.json_repository import JsonPolicyRepository
from .policy.service import PolicyService
from .survey.json_repository import JsonSurveyRepository
from .survey.service import SurveyService


def create_app(config=None):
    app = Flask(__name__)
    app.config.update(settings())
    if config:
        app.config.update(config)
    if app.config["STORAGE_DRIVER"] != "json":
        raise ValueError("Unsupported storage driver")
    if not isinstance(app.config["AUTH_LOCAL_HTTP"], bool):
        raise ValueError("AUTH_LOCAL_HTTP must be a boolean")
    auth_repository = JsonAuthRepository(data_path(app.config["DATA_FILE"]))
    auth_service = AuthService(auth_repository, generate_password_hash, check_password_hash)
    survey_repository = JsonSurveyRepository(survey_data_path(
        app.config["DATA_FILE"], app.config.get("SURVEY_DATA_FILE")))
    survey_service = SurveyService(survey_repository)
    policy_repository = JsonPolicyRepository(policy_data_path(
        app.config["DATA_FILE"], app.config.get("POLICY_DATA_FILE")))
    policy_service = PolicyService(policy_repository)
    app.register_blueprint(auth_routes(auth_service))
    app.register_blueprint(policy_routes(auth_service, policy_service))
    app.register_blueprint(survey_routes(auth_service, survey_service))
    return app
