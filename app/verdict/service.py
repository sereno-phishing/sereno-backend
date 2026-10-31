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
    signed-in user's history is a side effect that never fails the verdict."""

    def __init__(self, cache: CacheService, classifier: Classifier, history, timer=perf_counter):
        self.cache = cache
        self.classifier = classifier
        self.history = history
        self.timer = timer

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
        if username:
            self._record(username, verdict)
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

    def _record(self, username, verdict):
        try:
            self.history.record(username, dict(verdict))
        except Exception:
            logger.warning("History not recorded", exc_info=True)
