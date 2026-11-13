"""Deterministic two-class stand-in for the ML model, for tests and the
`CLASSIFIER=fake` setting. The demo scenario domains of the extension
(demo-store/domain/scenarios.js) get a fixed class and score, everything else
the legitimate FALLBACK. The extension mock's *.localhost test hosts are a
client development aid that "a real backend ignores", so they get FALLBACK too."""
from ..cache.domain import domain_of
from .classifier import LEGITIMATE, PHISHING, Classification


SCENARIOS = {
    "tienda-servicios.pe": Classification(LEGITIMATE, 0.04),
    "paypal-secure-login.com": Classification(PHISHING, 0.92),
    "pago-servicios-linea.net": Classification(PHISHING, 0.97),
}
FALLBACK = Classification(LEGITIMATE, 0.05)


class FakeClassifier:
    # Sereno.seed.modelVersion, the version the mock backend reports.
    version = "1.3"

    def classify(self, url):
        return SCENARIOS.get(domain_of(url), FALLBACK)
