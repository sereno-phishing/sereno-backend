import logging
from time import perf_counter

from ..cache.service import CacheService
from ..policy.domain import DEFAULT_POLICY
from .classifier import VERDICTS, Classification, Classifier
from .journal import ServedVerdict
from .url import InvalidUrl, cache_key, parse, site_domain


__all__ = ["ClassifierError", "InvalidUrl", "VerdictService"]

logger = logging.getLogger(__name__)
MAX_DOMAIN_LENGTH = 253


class ClassifierError(Exception):
    pass


class VerdictService:
    """Validates and normalizes the URL, then answers cache first (per-class
    TTL, invalidations) and with the classifier on a miss. The cache is keyed
    by the hash of the normalized URL and stores the model class, so the
    domain policy is read on every request and a policy edit never leaves a
    stale answer. Cache storage failures propagate as CacheStorageError
    (cache_down). The policy lookup falls back to the default policy; the
    signed-in user's history and the metrics query log are side effects that
    never fail the verdict. With a journal, one record per served verdict
    replaces those two writes (the store derives history and metrics from it)."""

    def __init__(self, cache: CacheService, classifier: Classifier, history, timer=perf_counter,
                 query_log=None, policies=None, journal=None):
        self.cache = cache
        self.classifier = classifier
        self.history = history
        self.timer = timer
        self.query_log = query_log
        self.policies = policies
        self.journal = journal

    def model_version(self):
        try:
            return self.classifier.version
        except Exception as error:  # a lazily loaded model can fail here
            logger.error("Model version unavailable: %s", error)
            raise ClassifierError from error

    def evaluate(self, url, username):
        parts = parse(url)
        domain = site_domain(parts.host)
        if len(domain) > MAX_DOMAIN_LENGTH:
            raise InvalidUrl
        key = cache_key(parts.normalized)
        started = self.timer()
        cached = self.cache.lookup(key)
        if cached is not None:
            verdict, score, source = cached.verdict, cached.score, "cache"
        else:
            result = self._classify(url)
            self.cache.store(key, domain, result.verdict, result.score)
            verdict, score, source = result.verdict, result.score, "modelo"
        policy = self._policy(domain)
        latency = max(0, round((self.timer() - started) * 1000))
        answer = {"domain": domain, "verdict": verdict, "policy": policy, "score": score,
                  "source": source, "latencyMs": latency}
        if self.journal is not None:
            self._side_effect("Query not recorded", self._journal, username, key, answer)
            return answer
        if self.query_log is not None:
            self._side_effect("Query not logged", self.query_log.record, dict(answer))
        if username:
            self._side_effect("History not recorded", self.history.record, username, dict(answer))
        return answer

    def _journal(self, username, key, answer):
        version = self.classifier.version if answer["source"] == "modelo" else None
        self.journal.record(username or None,
                            ServedVerdict(urlHash=key, modelVersion=version, **answer))

    def _classify(self, url):
        try:
            result = self.classifier.classify(url)
        except Exception as error:
            logger.error("Classifier failed: %s", error)
            raise ClassifierError from error
        if (not isinstance(result, Classification) or result.verdict not in VERDICTS
                or type(result.score) not in (int, float) or not 0 <= result.score <= 1):
            raise ClassifierError
        return result

    def _policy(self, domain):
        if self.policies is None:
            return DEFAULT_POLICY
        try:
            return self.policies.policy_for(domain)
        except Exception:
            # The verdict is the protection itself: without the policy store the
            # extension still warns instead of receiving no answer at all.
            logger.warning("Policy not read; default applied", exc_info=True)
            return DEFAULT_POLICY

    @staticmethod
    def _side_effect(message, action, *arguments):
        try:
            action(*arguments)
        except Exception:
            logger.warning(message, exc_info=True)
