import logging
from threading import Thread
from time import perf_counter

from flask import Flask
from werkzeug.security import check_password_hash, generate_password_hash

from .auth.json_repository import JsonAuthRepository
from .auth.service import AuthService
from .cache.json_repository import JsonCacheRepository
from .cache.service import CacheService
from .config import (CLASSIFIERS, cache_data_path, data_path, history_data_path, model_path,
                     policy_data_path, query_log_data_path, settings, survey_data_path)
from .history.json_repository import JsonHistoryRepository
from .history.service import HistoryService
from .http import (auth_routes, cache_routes, history_routes, metrics_routes, policy_routes,
                   survey_routes, verdict_routes)
from .metrics.json_repository import JsonQueryLogRepository
from .metrics.service import MetricsService
from .policy.json_repository import JsonPolicyRepository
from .policy.service import PolicyService
from .survey.json_repository import JsonSurveyRepository
from .survey.service import SurveyService
from .verdict.fake_classifier import FakeClassifier
from .verdict.model_classifier import ModelClassifier
from .verdict.service import VerdictService


def build_classifier(config):
    """The Classifier adapter the CLASSIFIER setting names. The model adapter
    is only checked here (artifact file and libraries present); loading the
    artifact is left to the background warm-up, or to the first request."""
    name = config["CLASSIFIER"]
    if name not in CLASSIFIERS:
        raise ValueError("CLASSIFIER must be 'model' or 'fake'")
    if name == "fake":
        return FakeClassifier()
    classifier = ModelClassifier(model_path(config["MODEL_PATH"]))
    classifier.check()
    return classifier


def start_daemon_thread(task):
    thread = Thread(target=task, name="model-warm-up", daemon=True)
    thread.start()
    return thread


def warm_up_task(classifier, logger, timer=perf_counter):
    """-> task() loading the model and logging the outcome. It never raises:
    it runs in a thread nobody joins, and a failed load is retried by the
    next request anyway."""
    def task():
        started = timer()
        try:
            classifier.warm_up()
            logger.info("Model %s loaded in %.1f s", classifier.version, timer() - started)
        except Exception as error:
            logger.warning("Model not preloaded, the next request retries: %s", error)
    return task


def create_app(config=None, clock=None, classifier=None, background=start_daemon_thread):
    """clock: optional () -> aware datetime for the cache, history and metrics services.
    classifier: a Classifier adapter to inject; by default the CLASSIFIER setting chooses.
    background: (task) -> handle, how the model warm-up is started; a daemon
    thread by default, so the server listens while the model loads."""
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
    cache_repository = JsonCacheRepository(cache_data_path(
        app.config["DATA_FILE"], app.config.get("CACHE_DATA_FILE")))
    clocked = [clock] if clock else []
    cache_service = CacheService(cache_repository, *clocked)
    history_repository = JsonHistoryRepository(history_data_path(
        app.config["DATA_FILE"], app.config.get("HISTORY_DATA_FILE")))
    history_service = HistoryService(history_repository, *clocked)
    metrics_service = MetricsService(JsonQueryLogRepository(query_log_data_path(
        app.config["DATA_FILE"], app.config.get("QUERY_LOG_DATA_FILE"))), *clocked)
    classifier = classifier or build_classifier(app.config)
    if isinstance(classifier, FakeClassifier) and not app.config.get("TESTING"):
        app.logger.warning("CLASSIFIER=fake: verdicts come from the stand-in, not the model")
    if callable(getattr(classifier, "warm_up", None)):
        # Preload so the first verdict does not pay the load (the extension
        # gives up after 4 s). The "loaded" line is the signal that the model
        # is ready, so make sure INFO is not filtered out for this logger.
        if not app.logger.isEnabledFor(logging.INFO):
            app.logger.setLevel(logging.INFO)
        app.extensions["model_warm_up"] = background(warm_up_task(classifier, app.logger))
    verdict_service = VerdictService(cache_service, classifier, history_service,
                                     query_log=metrics_service, policies=policy_service)
    app.register_blueprint(auth_routes(auth_service))
    app.register_blueprint(policy_routes(auth_service, policy_service))
    app.register_blueprint(survey_routes(auth_service, survey_service))
    app.register_blueprint(cache_routes(auth_service, cache_service))
    app.register_blueprint(verdict_routes(auth_service, verdict_service))
    app.register_blueprint(history_routes(auth_service, history_service))
    app.register_blueprint(metrics_routes(auth_service, metrics_service))
    return app
