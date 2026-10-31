"""Classifier port: the only seam the ML model (Stacking ensemble) plugs into."""
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class Classification:
    status: str  # "seguro" | "advertencia" | "bloqueo"
    score: float  # phishing probability, 0..1


class Classifier(Protocol):
    version: str

    def classify(self, url: str) -> Classification: ...
