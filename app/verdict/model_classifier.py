"""Classifier adapter over the trained Stacking model of the sereno-model
package (`sereno_model.models.stacking.StackingModel`, saved with joblib).

Nothing heavy happens at import or construction: the artifact and the ML
libraries it needs are loaded once, under a lock, by warm_up() (the app calls
it in the background at startup) or else on first use."""
from importlib.util import find_spec
from pathlib import Path
from threading import Lock

from .classifier import LEGITIMATE, PHISHING, Classification, ClassifierUnavailable


INSTALL_HINT = ("From sereno-backend/, run '.venv/Scripts/python.exe -m pip install -r "
                "requirements-model.txt' and '.venv/Scripts/python.exe -m pip install "
                "--no-deps -e ../sereno-model'.")
# Imported while unpickling the artifact (see requirements-model.txt).
REQUIRED_MODULES = ("joblib", "sklearn", "xgboost", "lightgbm", "pandas", "pyarrow", "tldextract")


class ModelUnavailable(ClassifierUnavailable):
    """The model artifact or the libraries it needs cannot be used."""


def load_artifact(path):
    import joblib  # deferred: pulls in the whole ML stack

    return joblib.load(path)


class ModelClassifier:
    def __init__(self, path, loader=None):
        """loader: optional (path) -> model, replacing joblib for tests."""
        self.path = Path(path)
        self.loader = loader
        self._model = None
        self._lock = Lock()

    def check(self):
        """Cheap startup check, without loading anything: the artifact file
        exists and the model libraries are installed."""
        self._require_file()
        missing = [name for name in REQUIRED_MODULES if find_spec(name) is None]
        if missing:
            raise ModelUnavailable(
                f"Model dependencies not installed ({', '.join(missing)}). {INSTALL_HINT}")

    def warm_up(self):
        """Loads the artifact now instead of on first use. Safe to call from
        another thread: a request arriving meanwhile waits for this same load."""
        self._loaded()

    @property
    def version(self):
        return self._loaded().model_version

    def classify(self, url):
        model = self._loaded()
        # One prediction at a time: the base models are not documented as
        # safe for concurrent prediction, and each already uses its own threads.
        with self._lock:
            score = float(model.score_url(url))
        return Classification(PHISHING if score >= model.threshold else LEGITIMATE, score)

    def _require_file(self):
        if not self.path.is_file():
            raise ModelUnavailable(
                f"Model artifact not found at '{self.path}'. Train or copy the artifact, or "
                "point MODEL_PATH at it (CLASSIFIER=fake runs without a model).")

    def _loaded(self):
        if self._model is None:
            with self._lock:
                if self._model is None:
                    self._model = self._load()
        return self._model

    def _load(self):
        self._require_file()
        try:
            return (self.loader or load_artifact)(self.path)
        except ImportError as error:
            raise ModelUnavailable(
                f"Model dependencies not installed ({error}). {INSTALL_HINT}") from error
        except Exception as error:
            raise ModelUnavailable(
                f"Model artifact '{self.path}' could not be loaded. It needs exactly the "
                "library versions pinned in requirements-model.txt.") from error
