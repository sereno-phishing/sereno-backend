"""Deterministic stand-in for the ML model. Reproduces the extension mock
(platform/providers/mock.js): demo scenario domains
(demo-store/domain/scenarios.js) get their scenario verdict, everything else
the mock FALLBACK. The mock's *.localhost test hosts are a client development
aid that "a real backend ignores", so they get FALLBACK here too.
Replace this adapter, not the service, to plug the real model."""
from ..cache.domain import domain_of
from .classifier import Classification


SCENARIOS = {
    "tienda-servicios.pe": Classification("seguro", 0.04),
    "paypal-secure-login.com": Classification("advertencia", 0.92),
    "pago-servicios-linea.net": Classification("bloqueo", 0.97),
}
FALLBACK = Classification("seguro", 0.05)


class FakeClassifier:
    # Sereno.seed.modelVersion, the version the mock backend reports.
    version = "1.3"

    def classify(self, url):
        return SCENARIOS.get(domain_of(url), FALLBACK)
