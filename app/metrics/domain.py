"""Operation metrics for the administration dashboard, computed from the log of
every verdict served (anonymous ones included). The output has exactly the
shape of the extension's Sereno.seed.metrics (platform/seed.js).

Every field is derived from served verdicts; none depends on the model's
quality (accuracy, F1 and the like are not part of the dashboard shape), so
no placeholder value is needed."""
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta


CAPACITY = 10000
LATENCY_TARGET_MS = 200
QUERY_ROWS = 50
HOURS = 24
DAYS = 7
# Bars fill 90 % of their box in the seed: 99 px of the 110 px chart and
# 32.4 px of the 36 px sparkline strip.
HOURLY_MAX_HEIGHT = 99.0
SPARKLINE_MAX_HEIGHT = 32.4
STATUSES = ("seguro", "advertencia", "bloqueo")  # ascending severity
PHISHING = frozenset({"advertencia", "bloqueo"})
SOURCE_LABELS = {"cache": "Caché", "modelo": "Modelo"}


class InvalidQueryEvent(Exception):
    pass


class QueryLogStorageError(Exception):
    pass


@dataclass(frozen=True)
class QueryEvent:
    domain: str
    status: str
    source: str
    latencyMs: int
    at: datetime

    def __post_init__(self):
        if (not isinstance(self.domain, str) or self.status not in STATUSES
                or self.source not in SOURCE_LABELS or type(self.latencyMs) is not int
                or not isinstance(self.at, datetime) or self.at.tzinfo is None):
            raise InvalidQueryEvent


def _within(events, start, end):
    return [item for item in events if start < item.at <= end]


def _percent(part, whole):
    return part * 100 / whole if whole else 0


def _mean_latency(events):
    return sum(item.latencyMs for item in events) / len(events) if events else 0


def _cache_hits(events):
    return sum(1 for item in events if item.source == "cache")


def _phishing(events):
    return sum(1 for item in events if item.status in PHISHING)


def _scale(values, maximum_height, digits):
    top = max(values)
    return [round(value / top * maximum_height, digits) if top and value else 0
            for value in values]


def _hourly(events, now):
    buckets = [_within(events, now - timedelta(hours=HOURS - index),
                       now - timedelta(hours=HOURS - 1 - index)) for index in range(HOURS)]
    heights = _scale([len(bucket) for bucket in buckets], HOURLY_MAX_HEIGHT, 1)
    bars = []
    for bucket, height in zip(buckets, heights):
        counts = Counter(item.status for item in bucket)
        # The most frequent result colours the bar; ties go to the more severe.
        level = max(STATUSES, key=lambda status: (counts[status], STATUSES.index(status)))
        bars.append({"level": level if bucket else "seguro", "height": height})
    return bars


def _sparklines(events, now):
    days = [_within(events, now - timedelta(days=DAYS - index),
                    now - timedelta(days=DAYS - 1 - index)) for index in range(DAYS)]
    series = {
        "indigo": [len(day) for day in days],
        "red": [_phishing(day) for day in days],
        "amber": [_mean_latency(day) for day in days],
        "green": [_percent(_cache_hits(day), len(day)) for day in days],
    }
    return {name: _scale(values, SPARKLINE_MAX_HEIGHT, 2) for name, values in series.items()}


def _queries(events, now):
    newest = sorted(events, key=lambda item: item.at, reverse=True)[:QUERY_ROWS]
    return [{"domain": item.domain,
             "stamp": item.at.astimezone(now.tzinfo).strftime("%d/%m/%Y %H:%M"),
             "source": SOURCE_LABELS[item.source], "latencyMs": item.latencyMs,
             "status": item.status} for item in newest]


def compute_metrics(events, now):
    """Dashboard metrics at `now` (aware): the stat cards cover the last 24 h
    (deltaPct against the 24 h before), the hourly chart the last 24 one-hour
    buckets, the sparklines the last 7 days, the table the newest 50 queries."""
    last = _within(events, now - timedelta(hours=HOURS), now)
    previous = _within(events, now - timedelta(hours=2 * HOURS), now - timedelta(hours=HOURS))
    phishing = _phishing(last)
    return {
        "analyzed": len(last),
        "deltaPct": round(_percent(len(last) - len(previous), len(previous))),
        "phishingPct": round(_percent(phishing, len(last)), 1),
        "phishingCount": phishing,
        "latencyMs": round(_mean_latency(last)),
        "latencyTargetMs": LATENCY_TARGET_MS,
        "cacheHitPct": round(_percent(_cache_hits(last), len(last))),
        "sparklines": _sparklines(events, now),
        "hourly": _hourly(events, now),
        "queries": _queries(events, now),
    }
