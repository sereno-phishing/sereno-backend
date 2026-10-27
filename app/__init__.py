from flask import Flask
from werkzeug.security import check_password_hash, generate_password_hash

from .auth.json_repository import JsonAuthRepository
from .auth.service import AuthService
from .config import data_path, settings
from .http import auth_routes


def create_app(config=None):
    app = Flask(__name__)
    app.config.update(settings())
    if config:
        app.config.update(config)
    if app.config["STORAGE_DRIVER"] != "json":
        raise ValueError("Unsupported storage driver")
    repository = JsonAuthRepository(data_path(app.config["DATA_FILE"]))
    service = AuthService(repository, generate_password_hash, check_password_hash)
    app.register_blueprint(auth_routes(service))
    return app
