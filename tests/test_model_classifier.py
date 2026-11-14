import sys
import time
from concurrent.futures import ThreadPoolExecutor
from importlib.util import find_spec
from pathlib import Path
from threading import Event

import pytest

from app import create_app
from app.config import BACKEND_ROOT, model_path, settings
from app.verdict import model_classifier
from app.verdict.classifier import Classification
from app.verdict.model_classifier import ModelClassifier, ModelUnavailable


ARTIFACT = BACKEND_ROOT.parent / "sereno-model" / "artifacts" / "2.0.0" / "model.joblib"


class StubModel:
    """The three members of sereno_model's StackingModel the adapter uses."""
    threshold = 0.45
    model_version = "7.7.7"

    def __init__(self, scores=None):
        self.scores = scores or {}
        self.urls = []

    def score_url(self, url):
        self.urls.append(url)
        return self.scores.get(url, 0.1)


class CountingLoader:
    def __init__(self, model=None, pause=0.0):
        self.model = model or StubModel()
        self.pause = pause
        self.paths = []

    def __call__(self, path):
        self.paths.append(path)
        time.sleep(self.pause)
        return self.model


@pytest.fixture
def artifact_file(tmp_path):
    path = tmp_path / "model.joblib"
    path.write_bytes(b"not a real artifact")
    return path


@pytest.fixture
def config(tmp_path):
    return {"TESTING": True, "DATA_FILE": str(tmp_path / "auth.json")}


def test_artifact_is_loaded_on_first_use_and_only_once(artifact_file):
    loader = CountingLoader()
    classifier = ModelClassifier(artifact_file, loader)
    assert loader.paths == []
    assert classifier.version == "7.7.7"
    classifier.classify("https://shop.pe/a")
    classifier.classify("https://shop.pe/b")
    assert loader.paths == [artifact_file]
    assert loader.model.urls == ["https://shop.pe/a", "https://shop.pe/b"]


@pytest.mark.parametrize("score, verdict", [(0.45, "phishing"), (0.99, "phishing"),
                                            (0.4499, "legitima"), (0.0, "legitima")])
def test_score_reaching_the_model_threshold_is_phishing(artifact_file, score, verdict):
    loader = CountingLoader(StubModel({"https://shop.pe/": score}))
    result = ModelClassifier(artifact_file, loader).classify("https://shop.pe/")
    assert result == Classification(verdict, score)
    assert type(result.score) is float


def test_concurrent_first_use_loads_the_artifact_once(artifact_file):
    loader = CountingLoader(pause=0.05)
    classifier = ModelClassifier(artifact_file, loader)
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(classifier.classify, [f"https://shop.pe/{n}" for n in range(8)]))
    assert len(loader.paths) == 1
    assert all(result.verdict == "legitima" for result in results)


def test_missing_artifact_is_a_clear_error_at_startup_and_on_first_use(tmp_path, config):
    missing = tmp_path / "absent" / "model.joblib"
    with pytest.raises(ModelUnavailable, match="MODEL_PATH"):
        create_app({**config, "CLASSIFIER": "model", "MODEL_PATH": str(missing)})
    loader = CountingLoader()
    with pytest.raises(ModelUnavailable, match="model.joblib"):
        ModelClassifier(missing, loader).classify("https://shop.pe/")
    assert loader.paths == []


def test_missing_model_dependencies_are_a_clear_error(artifact_file, monkeypatch):
    monkeypatch.setitem(sys.modules, "joblib", None)  # makes `import joblib` fail
    with pytest.raises(ModelUnavailable, match="requirements-model.txt"):
        ModelClassifier(artifact_file).check()
    with pytest.raises(ModelUnavailable, match="requirements-model.txt"):
        ModelClassifier(artifact_file).classify("https://shop.pe/")


def test_unreadable_artifact_is_a_model_error_and_is_retried(artifact_file):
    def broken(path):
        raise ValueError("bad pickle")
    classifier = ModelClassifier(artifact_file, broken)
    with pytest.raises(ModelUnavailable, match="could not be loaded"):
        classifier.classify("https://shop.pe/")
    classifier.loader = CountingLoader()
    assert classifier.version == "7.7.7"


# Settings and wiring

def test_classifier_defaults_to_the_fake_and_model_path_to_the_sibling_artifact(monkeypatch):
    monkeypatch.delenv("CLASSIFIER", raising=False)
    monkeypatch.delenv("MODEL_PATH", raising=False)
    assert settings()["CLASSIFIER"] == "fake"
    assert model_path(settings()["MODEL_PATH"]) == ARTIFACT
    monkeypatch.setenv("CLASSIFIER", "model")
    monkeypatch.setenv("MODEL_PATH", "models/custom.joblib")
    assert settings()["CLASSIFIER"] == "model"
    assert model_path(settings()["MODEL_PATH"]) == BACKEND_ROOT / "models" / "custom.joblib"


def test_default_app_serves_the_fake_without_touching_the_model(config, monkeypatch):
    monkeypatch.delenv("CLASSIFIER", raising=False)
    monkeypatch.setattr(model_classifier, "load_artifact", lambda path: pytest.fail("loaded"))
    client = create_app(config).test_client()
    assert client.get("/v1/model").get_json() == {"version": "1.3"}


def test_unknown_classifier_setting_fails_startup(config):
    with pytest.raises(ValueError, match="CLASSIFIER"):
        create_app({**config, "CLASSIFIER": "stacking"})


def test_model_setting_wires_the_adapter_and_an_unwarmed_model_loads_on_the_first_request(
        config, artifact_file, monkeypatch):
    loader = CountingLoader(StubModel({"https://bad.example/login": 0.93}))
    monkeypatch.setattr(model_classifier, "load_artifact", loader)
    app = create_app({**config, "CLASSIFIER": "model", "MODEL_PATH": str(artifact_file)},
                     background=lambda task: None)  # the warm-up never runs
    assert loader.paths == []
    client = app.test_client()
    body = client.post("/v1/verdict", json={"url": "https://bad.example/login"}).get_json()
    assert (body["verdict"], body["score"], body["source"]) == ("phishing", 0.93, "modelo")
    assert client.get("/v1/model").get_json() == {"version": "7.7.7"}
    assert loader.paths == [artifact_file]


def test_injected_classifier_wins_over_the_setting(config, tmp_path):
    class Injected:
        version = "injected"

        def classify(self, url):
            return Classification("legitima", 0.2)
    app = create_app({**config, "CLASSIFIER": "model", "MODEL_PATH": str(tmp_path / "absent")},
                     classifier=Injected())
    assert app.test_client().get("/v1/model").get_json() == {"version": "injected"}


def test_model_that_cannot_be_loaded_is_a_server_error_on_both_routes(config, artifact_file):
    # The placeholder file exists, so startup passes; it is not a pickle, so first use fails.
    if find_spec("joblib") is None:
        pytest.skip("model dependencies not installed")
    client = create_app({**config, "CLASSIFIER": "model",
                         "MODEL_PATH": str(artifact_file)}).test_client()
    for response in (client.post("/v1/verdict", json={"url": "https://shop.pe/"}),
                     client.get("/v1/model")):
        assert response.status_code == 500
        assert response.get_json() == {"error": "server"}


# Warm-up: the model is preloaded in the background when the app starts

class Tasks:
    """Stands in for the thread starter: collects the tasks instead of running them."""

    def __init__(self):
        self.tasks = []

    def __call__(self, task):
        self.tasks.append(task)


def model_app(config, artifact_file, **kwargs):
    return create_app({**config, "CLASSIFIER": "model", "MODEL_PATH": str(artifact_file)},
                      **kwargs)


def test_warm_up_loads_the_artifact_so_later_use_does_not(artifact_file):
    loader = CountingLoader()
    classifier = ModelClassifier(artifact_file, loader)
    classifier.warm_up()
    classifier.warm_up()
    classifier.classify("https://shop.pe/")
    assert loader.paths == [artifact_file]


def test_startup_schedules_one_warm_up_and_the_first_request_reuses_its_load(
        config, artifact_file, monkeypatch, caplog):
    loader = CountingLoader()
    monkeypatch.setattr(model_classifier, "load_artifact", loader)
    tasks = Tasks()
    app = model_app(config, artifact_file, background=tasks)
    assert len(tasks.tasks) == 1 and loader.paths == []
    with caplog.at_level("INFO"):
        tasks.tasks[0]()
    assert loader.paths == [artifact_file]
    finished = [record.getMessage() for record in caplog.records if "Model" in record.getMessage()]
    assert len(finished) == 1 and "7.7.7" in finished[0] and " s" in finished[0]
    assert app.test_client().post("/v1/verdict", json={"url": "https://shop.pe/"}).status_code == 200
    assert loader.paths == [artifact_file]


def test_nothing_is_warmed_for_the_fake_or_for_an_injected_classifier_without_warm_up(config):
    class Injected:
        version = "injected"

        def classify(self, url):
            return Classification("legitima", 0.2)
    tasks = Tasks()
    create_app({**config, "CLASSIFIER": "fake"}, background=tasks)
    create_app(config, classifier=Injected(), background=tasks)
    assert tasks.tasks == []


def test_injected_classifier_exposing_warm_up_is_warmed(config, artifact_file):
    loader = CountingLoader()
    tasks = Tasks()
    create_app(config, classifier=ModelClassifier(artifact_file, loader), background=tasks)
    tasks.tasks[0]()
    assert loader.paths == [artifact_file]


def test_failed_warm_up_is_logged_and_the_next_request_retries(config, artifact_file,
                                                               monkeypatch, caplog):
    def broken(path):
        raise ValueError("bad pickle")
    monkeypatch.setattr(model_classifier, "load_artifact", broken)
    tasks = Tasks()
    client = model_app(config, artifact_file, background=tasks).test_client()
    with caplog.at_level("WARNING"):
        tasks.tasks[0]()  # must not raise: it runs in a thread nobody joins
    assert any(record.levelname == "WARNING" and "could not be loaded" in record.getMessage()
               for record in caplog.records)
    monkeypatch.setattr(model_classifier, "load_artifact", CountingLoader())
    assert client.get("/v1/model").get_json() == {"version": "7.7.7"}


def test_default_starter_runs_the_warm_up_in_a_daemon_thread(config, artifact_file, monkeypatch):
    loader = CountingLoader()
    monkeypatch.setattr(model_classifier, "load_artifact", loader)
    app = model_app(config, artifact_file)
    thread = app.extensions["model_warm_up"]
    assert thread.daemon
    thread.join(timeout=10)
    assert not thread.is_alive() and loader.paths == [artifact_file]


def test_request_arriving_during_the_warm_up_waits_for_that_same_load(config, artifact_file,
                                                                      monkeypatch):
    release = Event()
    model = StubModel()
    paths = []

    def slow(path):
        paths.append(path)
        assert release.wait(timeout=10)
        return model
    monkeypatch.setattr(model_classifier, "load_artifact", slow)
    app = model_app(config, artifact_file)
    with ThreadPoolExecutor(max_workers=1) as pool:
        response = pool.submit(app.test_client().post, "/v1/verdict",
                               json={"url": "https://shop.pe/"})
        release.set()
        assert response.result(timeout=10).status_code == 200
    app.extensions["model_warm_up"].join(timeout=10)
    assert paths == [artifact_file]


# Integration: the real artifact (loads the ML libraries; takes several seconds)

def model_runtime_available():
    return ARTIFACT.is_file() and all(
        find_spec(name) is not None
        for name in ("joblib", "sklearn", "xgboost", "lightgbm", "pandas", "pyarrow", "tldextract"))


@pytest.mark.skipif(not model_runtime_available(),
                    reason="model artifact or its dependencies (requirements-model.txt) are absent")
def test_real_artifact_classifies_a_url_over_http(config):
    client = create_app({**config, "CLASSIFIER": "model",
                         "MODEL_PATH": str(ARTIFACT)}).test_client()
    response = client.post("/v1/verdict", json={"url": "https://www.example.org/pagos?id=1"})
    assert response.status_code == 200
    body = response.get_json()
    assert set(body) == {"domain", "verdict", "policy", "score", "source", "latencyMs"}
    assert body["domain"] == "example.org" and body["source"] == "modelo"
    assert body["policy"] == "advertencia"
    assert 0 <= body["score"] <= 1
    assert body["verdict"] == ("phishing" if body["score"] >= 0.45 else "legitima")
    assert client.get("/v1/model").get_json() == {"version": "2.0.0"}
    again = client.post("/v1/verdict", json={"url": "https://www.example.org/pagos?id=1"})
    assert (again.get_json()["source"], again.get_json()["score"]) == ("cache", body["score"])
