"""Classifier port: the only seam the ML model (Stacking ensemble) plugs into.

The model answers two classes. Whether a phishing verdict warns or blocks is
the domain policy, returned beside the verdict and applied by the extension."""
from dataclasses import dataclass
from typing import Protocol


PHISHING = "phishing"
LEGITIMATE = "legitima"
VERDICTS = (PHISHING, LEGITIMATE)


@dataclass(frozen=True)
class Classification:
    verdict: str  # "phishing" | "legitima"
    score: float  # phishing probability, 0..1


class Classifier(Protocol):
    version: str

    def classify(self, url: str) -> Classification: ...
