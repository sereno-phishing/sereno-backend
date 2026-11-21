"""The response-time experiment tool (tools/response_time_experiment.py) with
the HTTP layer and Redis faked: no network and no storage are needed."""
import csv
import json
from collections import Counter

import pytest

from tools import response_time_experiment as experiment


URLS = [f"https://site{number}.example/page" for number in range(400)]


# Workload: deterministic, with round(N*r) repetitions placed after the first occurrence

@pytest.mark.parametrize("level", [0, 25, 50, 75])
def test_the_workload_has_the_requested_share_of_repeated_requests(level):
    sequence = experiment.build_workload(URLS, requests=200, level=level, seed=7)
    repeated = round(200 * level / 100)
    assert len(sequence) == 200
    assert len(set(sequence)) == 200 - repeated
    assert sum(count - 1 for count in Counter(sequence).values()) == repeated
    assert set(sequence) <= set(URLS)


def test_the_workload_is_deterministic_for_a_seed_and_changes_with_it():
    first = experiment.build_workload(URLS, requests=100, level=50, seed=20261111)
    assert first == experiment.build_workload(URLS, requests=100, level=50, seed=20261111)
    assert first != experiment.build_workload(URLS, requests=100, level=50, seed=1)


def test_every_repetition_follows_the_first_occurrence_of_its_url():
    sequence = experiment.build_workload(URLS, requests=300, level=75, seed=3)
    first = {}
    for index, url in enumerate(sequence):
        first.setdefault(url, index)
    repeated_positions = [index for index, url in enumerate(sequence) if first[url] != index]
    assert len(repeated_positions) == 225
    # Spread over the whole sequence, not packed at its end.
    assert min(repeated_positions) < 60 and max(repeated_positions) > 240


def test_repeated_urls_and_positions_are_drawn_uniformly():
    """Two unique URLs u, v and one repetition: it repeats u or v with equal
    chance, and a repetition of u goes right after u or after v with equal
    chance (a repetition of v can only go last)."""
    outcomes = Counter()
    for seed in range(2000):
        first, second, third = experiment.build_workload(URLS[:2], requests=3, level=34,
                                                         seed=seed)
        outcomes["u, u, v" if first == second else
                 "u, v, u" if first == third else "u, v, v"] += 1
    shares = {key: count / 2000 for key, count in outcomes.items()}
    assert shares["u, v, v"] == pytest.approx(0.5, abs=0.05)
    assert shares["u, u, v"] == pytest.approx(0.25, abs=0.05)
    assert shares["u, v, u"] == pytest.approx(0.25, abs=0.05)
    assert experiment.build_workload(URLS[:1], requests=4, level=75, seed=5) == [URLS[0]] * 4


def test_the_repeated_count_rounds_half_up():
    assert experiment.repeated_requests(10, 25) == 3
    assert experiment.repeated_requests(1000, 75) == 750
    assert experiment.repeated_requests(1000, 0) == 0


def test_the_workload_needs_enough_unique_urls_and_a_level_below_100():
    with pytest.raises(ValueError, match="unique URLs"):
        experiment.build_workload(URLS[:10], requests=100, level=50, seed=1)
    with pytest.raises(ValueError, match="level"):
        experiment.build_workload(URLS, requests=10, level=100, seed=1)


def test_the_url_file_is_read_trimmed_without_blanks_comments_or_duplicates(tmp_path):
    path = tmp_path / "urls.txt"
    path.write_text("# test set\nhttps://a.pe/\n\n  https://b.pe/x  \nhttps://a.pe/\n",
                    encoding="utf-8")
    assert experiment.read_urls(path) == ["https://a.pe/", "https://b.pe/x"]


# Statistics and the equations of the paper

def test_summary_reports_count_mean_and_linear_percentiles():
    summary = experiment.summarize([10, 20, 30, 40, 50, 60, 70, 80, 90, 100])
    assert summary == {"count": 10, "mean": 55.0, "p50": 55.0, "p95": pytest.approx(95.5)}
    assert experiment.summarize([]) == {"count": 0, "mean": None, "p50": None, "p95": None}
    assert experiment.summarize([7.5]) == {"count": 1, "mean": 7.5, "p50": 7.5, "p95": 7.5}


def test_equation_1_weights_hit_and_miss_times_by_the_hit_ratio():
    assert experiment.predicted_mean(0.5, 10.0, 290.0) == pytest.approx(150.0)
    assert experiment.predicted_mean(0.0, None, 290.0) == 290.0
    assert experiment.predicted_mean(1.0, 10.0, None) == 10.0
    assert experiment.predicted_mean(0.5, None, 290.0) is None


def test_equation_2_gives_the_minimum_hit_ratio_only_when_misses_exceed_the_target():
    assert experiment.required_hit_ratio(10.0, 290.0, 200.0) == {
        "value": pytest.approx(90 / 280), "status": "computed"}
    # Tmiss <= Tmax: the target is met without any hit.
    assert experiment.required_hit_ratio(10.0, 150.0, 200.0) == {"value": 0.0,
                                                                 "status": "no_minimum"}
    assert experiment.required_hit_ratio(None, 200.0, 200.0) == {"value": 0.0,
                                                                 "status": "no_minimum"}
    # Hits no faster than the target: no hit ratio reaches it.
    assert experiment.required_hit_ratio(250.0, 290.0, 200.0) == {"value": None,
                                                                  "status": "unreachable"}
    assert experiment.required_hit_ratio(None, 290.0, 200.0) == {"value": None,
                                                                 "status": "unknown"}
    assert experiment.required_hit_ratio(10.0, None, 200.0) == {"value": None,
                                                                "status": "unknown"}


# Consistency check: cached verdicts against the run without cache

def row(index, url, source, verdict, score, mode="empty", level=50, latency=1.0):
    return {"level": level, "mode": mode, "index": index, "url": url, "source": source,
            "verdict": verdict, "score": score, "latencyMs": latency, "serverLatencyMs": 0,
            "status": 200}


def test_consistency_compares_every_cached_verdict_with_the_run_without_cache():
    reference = [row(0, "https://a.pe/", "modelo", "phishing", 0.9, mode="no-cache"),
                 row(1, "https://b.pe/", "modelo", "legitima", 0.1, mode="no-cache")]
    served = [row(0, "https://a.pe/", "modelo", "phishing", 0.9),
              row(1, "https://a.pe/", "cache", "phishing", 0.9),
              row(2, "https://b.pe/", "cache", "phishing", 0.1),
              row(3, "https://b.pe/", "cache", "legitima", 0.2),
              row(4, "https://c.pe/", "cache", "legitima", 0.2)]
    result = experiment.consistency(reference, served)
    assert result["compared"] == 3
    assert result["verdictMismatches"] == 1
    assert result["scoreMismatches"] == 1
    assert result["withoutReference"] == 1
    assert [example["index"] for example in result["examples"]] == [2, 3]


# Runs against a fake server

class FakeTimer:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


class FakeServer:
    """POST /v1/verdict with a cache that can be disabled. Hits take 10 ms
    and misses 290 ms of the fake timer; verdicts depend only on the URL."""

    def __init__(self, timer, cache_enabled=True, corrupt=False):
        self.timer = timer
        self.cache_enabled = cache_enabled
        self.corrupt = corrupt
        self.cache = {}
        self.calls = []

    @staticmethod
    def classify(url):
        number = int(url.split("site")[1].split(".")[0])
        return ("phishing", 0.9) if number % 3 == 0 else ("legitima", 0.1)

    def verdict(self, url):
        self.calls.append(url)
        if self.cache_enabled and url in self.cache:
            self.timer.now += 0.010
            verdict, score = self.cache[url]
            if self.corrupt:
                verdict = "legitima" if verdict == "phishing" else "phishing"
            return 200, {"domain": "x", "verdict": verdict, "policy": "advertencia",
                         "score": score, "source": "cache", "latencyMs": 1}
        self.timer.now += 0.290
        verdict, score = self.classify(url)
        if self.cache_enabled:
            self.cache[url] = (verdict, score)
        return 200, {"domain": "x", "verdict": verdict, "policy": "advertencia",
                     "score": score, "source": "modelo", "latencyMs": 250}


class FakeCache:
    def __init__(self, server):
        self.server = server
        self.cleared = 0

    def clear(self):
        self.cleared += 1
        removed = len(self.server.cache)
        self.server.cache.clear()
        return removed

    def count(self):
        return len(self.server.cache)


def run(server, mode, level=50, requests=40, seed=11):
    sequence = experiment.build_workload(URLS, requests=requests, level=level, seed=seed)
    return sequence, experiment.run_mode(server, FakeCache(server), sequence, mode=mode,
                                         level=level, timer=server.timer)


def test_the_run_without_cache_records_every_request_as_a_model_answer():
    server = FakeServer(FakeTimer(), cache_enabled=False)
    sequence, rows = run(server, "no-cache")
    assert [r["url"] for r in rows] == sequence
    assert [r["index"] for r in rows] == list(range(40))
    assert {r["source"] for r in rows} == {"modelo"}
    assert {r["mode"] for r in rows} == {"no-cache"} and {r["level"] for r in rows} == {50}
    assert all(r["latencyMs"] == pytest.approx(290.0) for r in rows)


def test_the_run_without_cache_refuses_a_server_that_answers_from_its_cache():
    server = FakeServer(FakeTimer(), cache_enabled=True)
    with pytest.raises(experiment.ExperimentError, match="CACHE_ENABLED=0"):
        run(server, "no-cache")


def test_the_empty_cache_run_clears_first_and_hits_only_repetitions():
    server = FakeServer(FakeTimer())
    server.cache[URLS[0]] = ("phishing", 0.9)
    sequence, rows = run(server, "empty")
    first = {}
    for index, url in enumerate(sequence):
        first.setdefault(url, index)
    assert [r["source"] == "cache" for r in rows] == [first[u] != i for i, u in enumerate(sequence)]
    assert len(server.calls) == 40


def test_the_warm_run_sends_an_unmeasured_pass_first_then_measures_only_hits():
    server = FakeServer(FakeTimer())
    sequence, rows = run(server, "warm")
    assert server.calls == sequence + sequence
    assert len(rows) == 40
    assert {r["source"] for r in rows} == {"cache"}


def test_cache_modes_refuse_a_server_whose_cache_is_disabled():
    server = FakeServer(FakeTimer(), cache_enabled=False)
    with pytest.raises(experiment.ExperimentError, match="cache"):
        run(server, "warm")


def test_a_failed_request_is_recorded_as_an_error_and_left_out_of_the_statistics():
    class Failing(FakeServer):
        def verdict(self, url):
            if len(self.calls) == 2:
                self.calls.append(url)
                return 500, {"error": "server"}
            return super().verdict(url)

    server = Failing(FakeTimer(), cache_enabled=False)
    _, rows = run(server, "no-cache", level=0, requests=5)
    assert [r["source"] for r in rows] == ["modelo", "modelo", "error", "modelo", "modelo"]
    stats = experiment.run_statistics(rows, target_ms=200.0)
    assert (stats["requests"], stats["errors"], stats["misses"]) == (5, 1, 4)


# The report

def full_rows(corrupt=False, levels=(0, 50)):
    rows = []
    for level in levels:
        for mode in experiment.MODES:
            server = FakeServer(FakeTimer(), cache_enabled=mode != "no-cache",
                                corrupt=corrupt)
            rows += run(server, mode, level=level)[1]
    return rows


def test_the_report_gives_hit_ratio_latencies_target_and_both_equations_per_run():
    report = experiment.build_report(full_rows(), target_ms=200.0,
                                     environment={"serverUrl": "http://fake"})
    assert report["environment"] == {"serverUrl": "http://fake"}
    level = next(item for item in report["levels"] if item["level"] == 50)
    assert (level["requests"], level["repeatedRequests"]) == (40, 20)
    assert level["reference"] == {"tHitMs": pytest.approx(10.0), "tHitSource": "warm",
                                  "tMissMs": pytest.approx(290.0), "tMissSource": "no-cache"}
    assert level["requiredHitRatio"] == {"value": pytest.approx(90 / 280), "status": "computed"}

    empty = level["runs"]["empty"]
    assert (empty["hits"], empty["misses"], empty["hitRatio"]) == (20, 20, 0.5)
    assert empty["latencyMs"]["hits"]["mean"] == pytest.approx(10.0)
    assert empty["latencyMs"]["misses"]["p95"] == pytest.approx(290.0)
    assert empty["latencyMs"]["all"]["mean"] == pytest.approx(150.0)
    assert empty["target"] == {"maxMeanMs": 200.0, "met": True}
    assert empty["meetsRequiredHitRatio"] is True
    assert empty["equation1"]["predictedMeanMs"] == pytest.approx(150.0)
    assert empty["equation1"]["measuredMeanMs"] == pytest.approx(150.0)
    assert empty["equation1"]["errorMs"] == pytest.approx(0.0)

    no_cache = level["runs"]["no-cache"]
    assert (no_cache["hitRatio"], no_cache["target"]["met"]) == (0.0, False)
    assert no_cache["latencyMs"]["hits"]["count"] == 0
    assert level["runs"]["warm"]["hitRatio"] == 1.0
    # The 20 hits of the empty-cache run and the 40 of the warm run.
    assert level["consistency"]["compared"] == 60
    assert level["consistency"]["verdictMismatches"] == 0


def test_the_report_counts_mismatches_of_a_cache_that_corrupts_verdicts():
    report = experiment.build_report(full_rows(corrupt=True, levels=(50,)), target_ms=200.0,
                                     environment={})
    assert report["levels"][0]["consistency"]["verdictMismatches"] == 60


def test_without_the_reference_runs_the_report_falls_back_to_the_runs_at_hand():
    rows = [r for r in full_rows(levels=(50,)) if r["mode"] == "empty"]
    level = experiment.build_report(rows, target_ms=200.0, environment={})["levels"][0]
    assert level["reference"]["tHitSource"] == "level"
    assert level["reference"]["tMissSource"] == "level"
    assert level["consistency"] is None


# Outputs and the command line

def test_outputs_are_a_raw_csv_and_a_json_report(tmp_path):
    rows = full_rows(levels=(25,))
    report = experiment.build_report(rows, target_ms=200.0, environment={"seed": 1})
    experiment.write_outputs(tmp_path, rows, report)
    with open(tmp_path / "requests.csv", newline="", encoding="utf-8") as handle:
        written = list(csv.DictReader(handle))
    assert len(written) == len(rows)
    assert list(written[0]) == list(experiment.CSV_FIELDS)
    assert json.loads((tmp_path / "report.json").read_text(encoding="utf-8")) == json.loads(
        json.dumps(report))
    assert experiment.read_rows(tmp_path / "requests.csv") == rows


def test_the_command_line_runs_the_modes_and_merges_later_invocations(tmp_path, monkeypatch):
    urls = tmp_path / "urls.txt"
    urls.write_text("\n".join(URLS[:60]) + "\n", encoding="utf-8")
    out = tmp_path / "out"
    timer = FakeTimer()
    servers = {}

    def connect(arguments):
        server = FakeServer(timer, cache_enabled="no-cache" not in arguments.modes)
        servers["last"] = server
        return server, FakeCache(server)

    monkeypatch.setattr(experiment, "connect", connect)
    monkeypatch.setattr(experiment, "perf_counter", timer)
    common = ["--urls", str(urls), "--requests", "20", "--levels", "0,50", "--out", str(out)]
    assert experiment.main(common + ["--modes", "no-cache"]) == 0
    assert experiment.main(common + ["--modes", "empty,warm"]) == 0
    report = json.loads((out / "report.json").read_text(encoding="utf-8"))
    assert [level["level"] for level in report["levels"]] == [0, 50]
    assert set(report["levels"][1]["runs"]) == {"no-cache", "empty", "warm"}
    assert report["environment"]["requests"] == 20
    assert report["environment"]["seed"] == experiment.DEFAULT_SEED
    assert len(report["environment"]["invocations"]) == 2
    assert len(experiment.read_rows(out / "requests.csv")) == 2 * 3 * 20

    # Another workload is refused instead of being mixed into the same results.
    assert experiment.main(common[:4] + ["--requests", "10"] + common[6:]
                           + ["--modes", "empty"]) == 2


def test_the_password_is_read_from_the_environment_never_from_the_command_line(monkeypatch):
    monkeypatch.setenv("SERENO_PASSWORD", "secret")
    arguments = experiment.parse_arguments(["--urls", "u.txt", "--out", "o",
                                            "--username", "alice"])
    assert experiment.password_of(arguments) == "secret"
    assert not any(action.dest == "password" for action in experiment.parser()._actions)
    monkeypatch.delenv("SERENO_PASSWORD")
    with pytest.raises(experiment.ExperimentError, match="SERENO_PASSWORD"):
        experiment.password_of(arguments)
