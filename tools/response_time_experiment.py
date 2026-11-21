"""Experiment 2 of the paper: response time of POST /v1/verdict with and without cache.

The workload is a sequence of N requests drawn from a file of test-set URLs
with a fixed seed, built at several repetition levels r: round(N*r) requests
(half up) repeat a URL already sent and the other requests are distinct URLs
sampled without replacement. Each repetition repeats one of those URLs chosen
uniformly at random, and is placed uniformly at random among the slots that
follow the first occurrence of that URL, so repetitions spread over the whole
sequence. Every level is generated from its own random.Random(seed), so a
level does not depend on which other levels are run.

Each sequence is sent in three modes:
  no-cache  the server started with CACHE_ENABLED=0: every verdict from the model;
  empty     the verdict cache cleared right before the run;
  warm      the cache cleared, the sequence sent once unmeasured, then measured.
The response time is measured at the client with time.perf_counter, from
sending the request to having the parsed verdict, as the extension's HTTP call
sees it. A request answered with source "cache" is a hit, "modelo" a miss.

The cache is cleared by deleting the verdict entries (keys named by a SHA-256
hex digest) and their per-domain index sets straight from the Redis of the
server: nothing in PostgreSQL is touched. The run without cache needs the
server restarted with CACHE_ENABLED=0, so it is a separate invocation with the
same --out folder; the runs are merged there and the report is rebuilt from
all of them. Another workload (URL file, N or seed) in that folder is refused.

Per level and mode the report gives the hit ratio h and the mean, p50 and p95
of the latency of hits, misses and all requests; whether the mean meets the
target (200 ms); the minimum hit ratio from equation (2),
h >= (Tmiss - Tmax) / (Tmiss - Thit), only defined when Tmiss > Tmax; and the
mean predicted by equation (1), T = h*Thit + (1 - h)*Tmiss, against the
measured one. Thit and Tmiss are the level's reference times: Tmiss from the
run without cache and Thit from the hits of the warm run, or else from the
hits and misses of every run of the level (source "level"); with the run's own
Thit and Tmiss, (1) would hold by construction. The consistency check compares
every verdict served from the cache with the verdict of the run without cache
for the same URL and level.

Usage, from sereno-backend/ (see the README):
  python -m tools.response_time_experiment --urls test_urls.txt --out results/exp2 --modes no-cache
  python -m tools.response_time_experiment --urls test_urls.txt --out results/exp2 --modes empty,warm
"""
import argparse
import csv
import hashlib
import http.client
import json
import math
import os
import platform
import random
import re
import sys
from datetime import datetime
from pathlib import Path
from time import perf_counter
from urllib.parse import urlsplit, urlunsplit


DEFAULT_SEED = 20261111
DEFAULT_REQUESTS = 1000
DEFAULT_LEVELS = (0, 25, 50, 75)
DEFAULT_SERVER = "http://127.0.0.1:5000"
# The Redis of docker-compose.yml, published on the loopback address.
DEFAULT_REDIS_URL = "redis://127.0.0.1:6379/0"
DEFAULT_TARGET_MS = 200.0
MODES = ("no-cache", "empty", "warm")
HIT, MISS, ERROR = "cache", "modelo", "error"
CSV_FIELDS = ("level", "mode", "index", "url", "source", "verdict", "score", "latencyMs",
              "serverLatencyMs", "status")
COOKIE_NAME = "sereno_session"
PASSWORD_ENV = "SERENO_PASSWORD"
# A cache entry is keyed by the SHA-256 hex digest of the normalized URL
# (app/verdict/url.py) and indexed by "dominio:<domain>" (app/cache/redis_repository.py).
ENTRY_KEY = re.compile(r"[0-9a-f]{64}")
INDEX_PREFIX = "dominio:"
MAX_EXAMPLES = 20


class ExperimentError(Exception):
    pass


# Workload

def read_urls(path):
    """The URLs of the file, one per line: trimmed, without blank lines,
    "#" comments or repeated URLs, in file order."""
    lines = Path(path).read_text(encoding="utf-8-sig").splitlines()
    return list(dict.fromkeys(line.strip() for line in lines
                              if line.strip() and not line.strip().startswith("#")))


def repeated_requests(requests, level):
    """round(requests * level / 100), half up, in integer arithmetic."""
    return (requests * level * 2 + 100) // 200


def build_workload(urls, requests, level, seed):
    """-> the sequence of `requests` URLs of repetition level `level` (percent)."""
    if not 0 <= level < 100:
        raise ValueError(f"the repetition level must be in [0, 100), not {level}")
    if requests < 1:
        raise ValueError("at least one request is needed")
    repeated = repeated_requests(requests, level)
    unique = requests - repeated
    if unique > len(urls):
        raise ValueError(f"level {level} needs {unique} unique URLs; the file has {len(urls)}")
    rng = random.Random(seed)
    uniques = rng.sample(list(urls), unique)
    sequence = list(uniques)
    for _ in range(repeated):
        url = uniques[rng.randrange(unique)]
        # Any slot after the first occurrence, the end included.
        sequence.insert(rng.randint(sequence.index(url) + 1, len(sequence)), url)
    return sequence


# Statistics and the equations of the paper

def percentile(ordered, fraction):
    """Linear interpolation between closest ranks (numpy's default)."""
    position = (len(ordered) - 1) * fraction
    low, high = math.floor(position), math.ceil(position)
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


def summarize(latencies):
    if not latencies:
        return {"count": 0, "mean": None, "p50": None, "p95": None}
    ordered = sorted(latencies)
    return {"count": len(ordered), "mean": sum(ordered) / len(ordered),
            "p50": percentile(ordered, 0.50), "p95": percentile(ordered, 0.95)}


def predicted_mean(hit_ratio, t_hit, t_miss):
    """Equation (1): T = h*Thit + (1 - h)*Tmiss; None when a needed term is unknown."""
    if hit_ratio is None:
        return None
    if hit_ratio == 0:
        return t_miss
    if hit_ratio == 1:
        return t_hit
    if t_hit is None or t_miss is None:
        return None
    return hit_ratio * t_hit + (1 - hit_ratio) * t_miss


def required_hit_ratio(t_hit, t_miss, t_max):
    """Equation (2): the hit ratio from which the mean is at most t_max."""
    if t_miss is None:
        return {"value": None, "status": "unknown"}
    if t_miss <= t_max:
        return {"value": 0.0, "status": "no_minimum"}
    if t_hit is None:
        return {"value": None, "status": "unknown"}
    if t_hit > t_max:
        return {"value": None, "status": "unreachable"}
    return {"value": (t_miss - t_max) / (t_miss - t_hit), "status": "computed"}


def consistency(reference_rows, served_rows):
    """Every verdict served from the cache against the model's verdict for the
    same URL in the run without cache."""
    reference = {row["url"]: (row["verdict"], row["score"])
                 for row in reference_rows if row["source"] == MISS}
    result = {"compared": 0, "verdictMismatches": 0, "scoreMismatches": 0,
              "withoutReference": 0, "examples": []}
    for row in served_rows:
        if row["source"] != HIT:
            continue
        if row["url"] not in reference:
            result["withoutReference"] += 1
            continue
        result["compared"] += 1
        verdict, score = reference[row["url"]]
        verdict_differs = row["verdict"] != verdict
        score_differs = row["score"] != score
        result["verdictMismatches"] += verdict_differs
        result["scoreMismatches"] += score_differs
        if (verdict_differs or score_differs) and len(result["examples"]) < MAX_EXAMPLES:
            result["examples"].append({
                "mode": row["mode"], "index": row["index"], "url": row["url"],
                "verdict": row["verdict"], "score": row["score"],
                "referenceVerdict": verdict, "referenceScore": score})
    return result


def latencies(rows, source=None):
    return [row["latencyMs"] for row in rows
            if row["source"] != ERROR and (source is None or row["source"] == source)]


def run_statistics(rows, target_ms):
    answered = [row for row in rows if row["source"] != ERROR]
    hits = sum(row["source"] == HIT for row in answered)
    total = summarize(latencies(rows))
    return {"requests": len(rows), "errors": len(rows) - len(answered), "hits": hits,
            "misses": len(answered) - hits,
            "hitRatio": hits / len(answered) if answered else None,
            "latencyMs": {"hits": summarize(latencies(rows, HIT)),
                          "misses": summarize(latencies(rows, MISS)), "all": total},
            "target": {"maxMeanMs": target_ms,
                       "met": None if total["mean"] is None else total["mean"] <= target_ms}}


def reference_times(level_rows):
    """(Thit, its source, Tmiss, its source) of one level."""
    def mean(rows, source=None):
        values = latencies(rows, source)
        return sum(values) / len(values) if values else None

    by_mode = {mode: [row for row in level_rows if row["mode"] == mode] for mode in MODES}
    t_hit, hit_source = mean(by_mode["warm"], HIT), "warm"
    if t_hit is None:
        t_hit, hit_source = mean(level_rows, HIT), "level"
    t_miss, miss_source = mean(by_mode["no-cache"], MISS), "no-cache"
    if t_miss is None:
        t_miss, miss_source = mean(level_rows, MISS), "level"
    return (t_hit, hit_source if t_hit is not None else None,
            t_miss, miss_source if t_miss is not None else None)


def meets(hit_ratio, required):
    if required["status"] == "no_minimum":
        return True
    if required["status"] == "unreachable":
        return False
    if required["value"] is None or hit_ratio is None:
        return None
    return hit_ratio >= required["value"]


def build_report(rows, target_ms, environment):
    levels = []
    for level in sorted({row["level"] for row in rows}):
        level_rows = [row for row in rows if row["level"] == level]
        modes = [mode for mode in MODES if any(row["mode"] == mode for row in level_rows)]
        sequence = [row["url"] for row in sorted(
            (row for row in level_rows if row["mode"] == modes[0]), key=lambda row: row["index"])]
        t_hit, hit_source, t_miss, miss_source = reference_times(level_rows)
        required = required_hit_ratio(t_hit, t_miss, target_ms)
        runs = {}
        for mode in modes:
            mode_rows = [row for row in level_rows if row["mode"] == mode]
            stats = run_statistics(mode_rows, target_ms)
            measured = stats["latencyMs"]["all"]["mean"]
            predicted = predicted_mean(stats["hitRatio"], t_hit, t_miss)
            error = None if predicted is None or measured is None else measured - predicted
            stats["equation1"] = {
                "hitRatio": stats["hitRatio"], "tHitMs": t_hit, "tMissMs": t_miss,
                "predictedMeanMs": predicted, "measuredMeanMs": measured, "errorMs": error,
                "relativeError": error / measured if error is not None and measured else None}
            stats["meetsRequiredHitRatio"] = meets(stats["hitRatio"], required)
            runs[mode] = stats
        reference_rows = [row for row in level_rows if row["mode"] == "no-cache"]
        levels.append({
            "level": level, "requests": len(sequence),
            "repeatedRequests": len(sequence) - len(set(sequence)),
            "reference": {"tHitMs": t_hit, "tHitSource": hit_source,
                          "tMissMs": t_miss, "tMissSource": miss_source},
            "requiredHitRatio": required, "runs": runs,
            "consistency": consistency(reference_rows, [
                row for row in level_rows if row["mode"] != "no-cache"])
            if reference_rows else None})
    return {"experiment": "response-time", "targetMs": target_ms, "environment": environment,
            "levels": levels}


# Runs

def send(client, url, timer):
    started = timer()
    try:
        status, body = client.verdict(url)
    except (OSError, http.client.HTTPException) as error:
        status, body = 0, {"error": type(error).__name__}
    elapsed = round((timer() - started) * 1000, 3)
    answered = (status == 200 and isinstance(body, dict)
                and body.get("source") in (HIT, MISS))
    return {"source": body["source"] if answered else ERROR,
            "verdict": body.get("verdict") if answered else None,
            "score": body.get("score") if answered else None,
            "latencyMs": elapsed,
            "serverLatencyMs": body.get("latencyMs") if answered else None,
            "status": status}


def run_mode(client, cache, sequence, mode, level, timer=perf_counter):
    """Sends the sequence in one mode -> one row per measured request. Raises
    ExperimentError when the server's answers contradict the mode."""
    if mode not in MODES:
        raise ValueError(f"unknown mode {mode!r}")
    cache.clear()
    if mode == "warm":
        for url in sequence:  # unmeasured pass that fills the cache
            send(client, url, timer)
    rows = [{"level": level, "mode": mode, "index": index, "url": url,
             **send(client, url, timer)} for index, url in enumerate(sequence)]
    hits = [row for row in rows if row["source"] == HIT]
    if mode == "no-cache":
        left = cache.count()
        if hits or left:
            raise ExperimentError(
                f"level {level}: the run without cache got {len(hits)} cached answers and "
                f"left {left} cache entries; restart the server with CACHE_ENABLED=0")
    else:
        if not hits and (mode == "warm" or len(set(sequence)) < len(sequence)):
            raise ExperimentError(
                f"level {level}, {mode}: no answer came from the cache; is the server "
                "running with CACHE_ENABLED=0?")
        seen = set()
        for row in rows:
            if mode == "empty" and row["url"] not in seen and row["source"] == HIT:
                raise ExperimentError(f"level {level}: the cache was not empty "
                                      f"(request {row['index']} hit on its first send)")
            seen.add(row["url"])
    return rows


# Adapters: the HTTP call of the extension and the Redis of the server

class HttpVerdictClient:
    """POST /v1/verdict over one keep-alive connection, with the headers of
    the extension's remote adapter and, after login(), its session cookie."""

    def __init__(self, base_url, timeout=10.0):
        parts = urlsplit(base_url)
        if parts.scheme not in ("http", "https") or not parts.hostname:
            raise ExperimentError(f"not an http(s) server URL: {base_url}")
        kind = http.client.HTTPSConnection if parts.scheme == "https" else http.client.HTTPConnection
        self.connection = kind(parts.hostname, parts.port, timeout=timeout)
        self.prefix = parts.path.rstrip("/")
        self.token = None

    def _post(self, path, payload):
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if self.token:
            headers["Cookie"] = f"{COOKIE_NAME}={self.token}"
        body = json.dumps(payload).encode("utf-8")
        for attempt in (1, 2):
            try:
                self.connection.request("POST", self.prefix + path, body=body, headers=headers)
                response = self.connection.getresponse()
                data = response.read()
                break
            except (http.client.RemoteDisconnected, ConnectionResetError, BrokenPipeError):
                # The server closed the idle keep-alive connection: reopen once.
                self.connection.close()
                if attempt == 2:
                    raise
            except (OSError, http.client.HTTPException):
                self.connection.close()
                raise
        try:
            parsed = json.loads(data)
        except ValueError:
            parsed = None
        return response.status, parsed

    def verdict(self, url):
        return self._post("/v1/verdict", {"url": url})

    def login(self, username, password):
        status, body = self._post("/v1/auth/login", {"username": username, "password": password})
        if status != 200 or not isinstance(body, dict) or not isinstance(body.get("token"), str):
            raise ExperimentError(f"login as {username!r} failed (HTTP {status})")
        self.token = body["token"]


class RedisVerdictCache:
    """The verdict entries in the Redis of the server, found with SCAN."""

    def __init__(self, url):
        import redis

        self.errors = redis.RedisError
        self.client = redis.Redis.from_url(url, decode_responses=True,
                                           socket_connect_timeout=2, socket_timeout=10)
        self.url = url

    def _keys(self):
        try:
            return [key for key in self.client.scan_iter(count=1000)
                    if ENTRY_KEY.fullmatch(key) or key.startswith(INDEX_PREFIX)]
        except self.errors as error:
            raise ExperimentError(f"Redis unreachable at {public_url(self.url)}: {error}")

    def clear(self):
        """Deletes the verdict entries and the domain indexes -> how many keys."""
        keys = self._keys()
        try:
            for start in range(0, len(keys), 500):
                self.client.delete(*keys[start:start + 500])
        except self.errors as error:
            raise ExperimentError(f"Redis unreachable at {public_url(self.url)}: {error}")
        return len(keys)

    def count(self):
        return sum(1 for key in self._keys() if ENTRY_KEY.fullmatch(key))


def public_url(url):
    """The URL without user and password, for the report."""
    parts = urlsplit(url)
    host = parts.hostname or ""
    netloc = host + (f":{parts.port}" if parts.port else "")
    return urlunsplit((parts.scheme, netloc, parts.path, "", ""))


# Outputs

def write_outputs(out_dir, rows, report):
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    with open(out / "requests.csv", "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: "" if row[field] is None else row[field]
                             for field in CSV_FIELDS})
    (out / "report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n",
                                     encoding="utf-8")


def read_rows(path):
    def number(kind):
        return lambda value: None if value == "" else kind(value)

    kinds = {"level": int, "index": int, "status": int, "latencyMs": float,
             "score": number(float), "serverLatencyMs": number(int),
             "verdict": lambda value: value or None}
    with open(path, newline="", encoding="utf-8") as handle:
        return [{field: kinds.get(field, str)(record[field]) for field in CSV_FIELDS}
                for record in csv.DictReader(handle)]


# Command line

def parser():
    commands = argparse.ArgumentParser(
        prog="python -m tools.response_time_experiment",
        description="Experiment 2: response time of POST /v1/verdict with and without cache.")
    commands.add_argument("--urls", required=True,
                          help="file of test-set URLs, one per line")
    commands.add_argument("--out", required=True,
                          help="folder of requests.csv, report.json and workload.json")
    commands.add_argument("--requests", type=int, default=DEFAULT_REQUESTS,
                          help=f"requests per sequence (default {DEFAULT_REQUESTS})")
    commands.add_argument("--seed", type=int, default=DEFAULT_SEED,
                          help=f"workload seed (default {DEFAULT_SEED})")
    commands.add_argument("--levels", default=",".join(map(str, DEFAULT_LEVELS)),
                          help="repetition levels in percent (default 0,25,50,75)")
    commands.add_argument("--modes", default=",".join(MODES),
                          help="no-cache (server with CACHE_ENABLED=0), empty, warm")
    commands.add_argument("--server", default=DEFAULT_SERVER,
                          help=f"server base URL (default {DEFAULT_SERVER})")
    commands.add_argument("--redis-url", default=os.environ.get("SERENO_REDIS_URL",
                                                                DEFAULT_REDIS_URL),
                          help="the server's Redis, to clear the verdict cache "
                               f"(default SERENO_REDIS_URL or {DEFAULT_REDIS_URL})")
    commands.add_argument("--target-ms", type=float, default=DEFAULT_TARGET_MS,
                          help="target mean response time Tmax (default 200)")
    commands.add_argument("--timeout", type=float, default=10.0,
                          help="seconds before a request counts as an error (default 10)")
    commands.add_argument("--username", default=os.environ.get("SERENO_USERNAME"),
                          help="send the verdicts signed in as this account, like a "
                               "signed-in extension (default SERENO_USERNAME; anonymous "
                               f"without it); the password is read from {PASSWORD_ENV}")
    return commands


def parse_arguments(argv):
    arguments = parser().parse_args(argv)
    try:
        arguments.levels = sorted({int(level) for level in arguments.levels.split(",")})
    except ValueError:
        raise ExperimentError(f"--levels must be integers: {arguments.levels}")
    arguments.modes = [mode.strip() for mode in arguments.modes.split(",") if mode.strip()]
    unknown = [mode for mode in arguments.modes if mode not in MODES]
    if unknown or not arguments.modes:
        raise ExperimentError(f"--modes must be among {', '.join(MODES)}")
    if arguments.requests < 1:
        raise ExperimentError("--requests must be at least 1")
    return arguments


def password_of(arguments):
    password = os.environ.get(PASSWORD_ENV)
    if not password:
        raise ExperimentError(f"--username {arguments.username} needs the password in "
                              f"the {PASSWORD_ENV} environment variable")
    return password


def connect(arguments):
    """-> (verdict client, cache) for the server and the Redis of the arguments."""
    client = HttpVerdictClient(arguments.server, arguments.timeout)
    if arguments.username:
        client.login(arguments.username, password_of(arguments))
    return client, RedisVerdictCache(arguments.redis_url)


def now():
    return datetime.now().astimezone().isoformat(timespec="seconds")


def print_summary(report, stream):
    print(f"{'level':>5} {'mode':>8} {'h':>6} {'mean':>8} {'p50':>8} {'p95':>8} "
          f"{'target':>6} {'h min':>6} {'T(1)':>8}", file=stream)

    def fmt(value, pattern):
        return "-" if value is None else format(value, pattern)

    for level in report["levels"]:
        for mode, run in level["runs"].items():
            total = run["latencyMs"]["all"]
            print(f"{level['level']:>5} {mode:>8} {fmt(run['hitRatio'], '.3f'):>6} "
                  f"{fmt(total['mean'], '.1f'):>8} {fmt(total['p50'], '.1f'):>8} "
                  f"{fmt(total['p95'], '.1f'):>8} "
                  f"{'yes' if run['target']['met'] else 'no':>6} "
                  f"{fmt(level['requiredHitRatio']['value'], '.3f'):>6} "
                  f"{fmt(run['equation1']['predictedMeanMs'], '.1f'):>8}", file=stream)


def main(argv=None):
    try:
        arguments = parse_arguments(argv)
        urls = read_urls(arguments.urls)
        workload = {"urlFileSha256": hashlib.sha256("\n".join(urls).encode("utf-8")).hexdigest(),
                    "urlsAvailable": len(urls), "requests": arguments.requests,
                    "seed": arguments.seed}
        out = Path(arguments.out)
        state_path = out / "workload.json"
        state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else {}
        if state and state["workload"] != workload:
            raise ExperimentError(f"{out} holds the results of another workload "
                                  f"({state['workload']}); use another --out")
        sequences = {level: build_workload(urls, arguments.requests, level, arguments.seed)
                     for level in arguments.levels}
        previous = read_rows(out / "requests.csv") if state and (out / "requests.csv").exists() \
            else []
        client, cache = connect(arguments)
        started, rows = now(), []
        for level in arguments.levels:
            for mode in arguments.modes:
                print(f"level {level}%, {mode}: {arguments.requests} requests", file=sys.stderr)
                rows += run_mode(client, cache, sequences[level], mode, level,
                                 timer=perf_counter)
        done = {(level, mode) for level in arguments.levels for mode in arguments.modes}
        rows = sorted([row for row in previous if (row["level"], row["mode"]) not in done] + rows,
                      key=lambda row: (row["level"], MODES.index(row["mode"]), row["index"]))
        invocations = state.get("invocations", []) + [{
            "startedAt": started, "finishedAt": now(), "serverUrl": public_url(arguments.server),
            "levels": arguments.levels, "modes": arguments.modes,
            "signedIn": bool(arguments.username),
            "cacheCleared": f"SCAN + DEL of the verdict keys at "
                            f"{public_url(arguments.redis_url)} before every run"}]
        environment = {**workload, "urlFile": str(arguments.urls),
                       "targetMs": arguments.target_ms,
                       "timer": "time.perf_counter at the client, from sending the request "
                                "to the parsed verdict",
                       "python": platform.python_version(), "platform": platform.platform(),
                       "invocations": invocations}
        report = build_report(rows, arguments.target_ms, environment)
        write_outputs(out, rows, report)
        state_path.write_text(json.dumps({"workload": workload, "invocations": invocations},
                                         indent=2) + "\n", encoding="utf-8")
        print_summary(report, sys.stdout)
        return 0
    except (ExperimentError, ValueError, OSError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
