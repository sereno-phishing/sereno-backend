import logging
from time import perf_counter

from ..cache.domain import CacheStorageError
from ..cache.service import CacheService
from ..policy.domain import DEFAULT_POLICY
from .classifier import VERDICTS, Classification, Classifier
from .journal import ServedVerdict, VerdictJournal
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
    stale answer. A cache that cannot be read or written never fails the
    verdict (RF-20): the classifier answers, the answer says "modelo" and the
    incident is logged; the cache is used again as soon as it responds. The
    policy lookup falls back to the default policy. Every
    served verdict is recorded in the journal, which backs the signed-in
    user's history and the metrics; that record is a side effect that never
    fails the verdict."""

    def __init__(self, cache: CacheService, classifier: Classifier, journal: VerdictJournal,
                 timer=perf_counter, policies=None):
        self.cache = cache
        self.classifier = classifier
        self.journal = journal
        self.timer = timer
        self.policies = policies

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
        cached, outage = None, None
        try:
            cached = self.cache.lookup(key)
        except CacheStorageError as error:
            outage = ("read", error)
        if cached is not None:
            verdict, score, source = cached.verdict, cached.score, "cache"
        else:
            result = self._classify(url)
            if outage is None:  # no second wait on a cache that just failed
                try:
                    self.cache.store(key, domain, result.verdict, result.score)
                except CacheStorageError as error:
                    outage = ("write", error)
            verdict, score, source = result.verdict, result.score, "modelo"
        if outage:
            # Once per request, with the hash and the domain, never the URL.
            operation, error = outage
            logger.warning(
                "Cache unavailable on %s; verdict served by the model "
                "(domain=%s, urlHash=%s): %s", operation, domain, key, error.__cause__ or error)
        policy = self._policy(domain)
        latency = max(0, round((self.timer() - started) * 1000))
        answer = {"domain": domain, "verdict": verdict, "policy": policy, "score": score,
                  "source": source, "latencyMs": latency}
        self._record(username, key, answer)
        return answer

    def _record(self, username, key, answer):
        try:
            version = self.classifier.version if answer["source"] == "modelo" else None
            self.journal.record(username or None,
                                ServedVerdict(urlHash=key, modelVersion=version, **answer))
        except Exception:
            logger.warning("Query not recorded", exc_info=True)

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
