import logging
from time import perf_counter

from ..cache.domain import VERDICTS, domain_of
from ..cache.service import CacheService
from .classifier import Classification, Classifier


logger = logging.getLogger(__name__)


class InvalidUrl(Exception):
    pass


class ClassifierError(Exception):
    pass


class VerdictService:
    """Cache first (per-status TTL, invalidations), the classifier on a miss.
    Cache storage failures propagate as CacheStorageError (cache_down). The
    signed-in user's history and the metrics query log are side effects that
    never fail the verdict."""

    def __init__(self, cache: CacheService, classifier: Classifier, history, timer=perf_counter,
                 query_log=None):
        self.cache = cache
        self.classifier = classifier
        self.history = history
        self.timer = timer
        self.query_log = query_log

    def model_version(self):
        return self.classifier.version

    def evaluate(self, url, username):
        if not isinstance(url, str):
            raise InvalidUrl
        domain = domain_of(url)
        if not domain or len(domain) > 253:
            raise InvalidUrl
        started = self.timer()
        cached = self.cache.lookup(domain)
        if cached is not None:
            status, score, source = cached.status, cached.score, "cache"
        else:
            result = self._classify(url)
            self.cache.store(domain, result.status, result.score)
            status, score, source = result.status, result.score, "modelo"
        latency = max(0, round((self.timer() - started) * 1000))
        verdict = {"domain": domain, "status": status, "score": score,
                   "source": source, "latencyMs": latency}
        if self.query_log is not None:
            self._side_effect("Query not logged", self.query_log.record, dict(verdict))
        if username:
            self._side_effect("History not recorded", self.history.record, username, dict(verdict))
        return verdict

    def _classify(self, url):
        try:
            result = self.classifier.classify(url)
        except Exception as error:
            raise ClassifierError from error
        if (not isinstance(result, Classification) or result.status not in VERDICTS
                or type(result.score) not in (int, float) or not 0 <= result.score <= 1):
            raise ClassifierError
        return result

    @staticmethod
    def _side_effect(message, action, *arguments):
        try:
            action(*arguments)
        except Exception:
            logger.warning(message, exc_info=True)
