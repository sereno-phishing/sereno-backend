# Real model classifier

## Objective
Serve verdicts from the trained Stacking model (`sereno-model`, artifact 2.0.0) instead of `FakeClassifier`, so the product works end to end locally.

## Problem and rationale
The verdict pipeline runs on a three-state fake. The ADD defines a two-class model (phishing / legitimate), a cache keyed by the normalized URL (RFC 3986) and a per-domain response policy applied by the extension (RF-10). Connecting the model closes I-46 (cache key) and I-49 (two classes).

## Decisions
- Policy is applied by the extension (Andrés, 2026-11-13). The backend returns the model class plus the domain policy.
- Cache key: SHA-256 of `sereno_model.data.normalize.normalize_url(url)`; the entry keeps the domain, never the URL (RNF-05). Decision I-46 (Andrés, 2026-11-02).
- The cache stores the model class, so a policy edit never leaves a stale entry.
- Admin invalidation stays per domain and drops every entry of that host.
- The backend depends on the `sereno-model` package: the pickle needs it importable.
- The blind test split is not read by this work.

## REST contract changes
- `POST /v1/verdict` `{url}` -> `{domain, verdict, policy, score, source, latencyMs}`; `verdict` is `"phishing"` or `"legitima"`; `policy` is the domain policy (`"advertencia"` or `"bloqueo"`, default `"advertencia"`), always present; `source` is `"cache"` or `"modelo"`. A URL the normalizer rejects is a 400.
- History items: `status` is replaced by `verdict` and `policy` (the policy in force when served).
- Cache entries (`GET /v1/cache`): `status` is replaced by `verdict`; several entries may share a domain.
- `GET /v1/metrics`: response shape unchanged, computed from the stored `verdict` and `policy`.
- `GET /v1/model`: reports the loaded model version (`2.0.0`).

## Authorized scope
- sereno-backend only: `app/`, `tests/`, `requirements*.txt`, `start.ps1`, `README.md`, this file.
- No Redis, no PostgreSQL, no remote operations, no academic documents, no change in `sereno-model`.

## Constraints
English technical artifacts. Test first (pytest), RED before GREEN. Conventional commits, no AI attribution. Tests keep injecting a stand-in classifier; the real artifact is loaded lazily and only in a test that skips when it is absent.

## Work units
- [x] T1 Two-class port and pipeline: `Classification(verdict, score)`, URL validation and normalized-URL cache key, policy lookup in the response, history, cache and metrics on `verdict` + `policy`. Route: delegated (multi-file write).
- [x] T2 `ModelClassifier` adapter: lazy `joblib.load`, `CLASSIFIER` (`model` | `fake`) and `MODEL_PATH` settings, model dependencies, `start.ps1` and README. Route: delegated.
- [ ] T3 Local end-to-end check with the real artifact over HTTP. Route: parent spot check.

## Checks
`.venv/Scripts/python.exe -m pytest -q` (319 passed before this work).

## Progress and evidence
Branch `feat/real-model-classifier` from `48f6909`.

### T1 (route: delegated writer) — commit `7a63643`
- RED: after rewriting the tests, `pytest -q --continue-on-collection-errors` gave 35 failed, 271 passed, 1 collection error (`tests/test_verdict.py`: `No module named 'app.verdict.url'`).
- GREEN: `.venv/Scripts/python.exe -m pytest -q` -> 355 passed.
- The normalizer is imported from `sereno_model.data.normalize` behind `app/verdict/url.py`; that module only needs the standard library, so the suite loads no ML dependency. The package itself must be installed (`pip install --no-deps -e ../sereno-model`).
- Interpretation of the contract line on `GET /v1/cache`: that route never exposed entries, and still does not. Stored entries carry `verdict`; the `status` of an invalidation row is its label (`"Reevaluado: <verdict>"`) and keeps its name.
- Added without being in the task text, so the local `data/` files keep working: history and query-log records with the former three-state `status` are upgraded on read; cache entries keyed by domain are dropped on read.
- A policy store failure during a verdict answers with the default policy and logs a warning instead of failing the verdict.

### T2 (route: delegated writer) — commit `aae046a`
- RED: `pytest -q tests/test_model_classifier.py` -> collection error (`cannot import name 'model_path' from 'app.config'`; no `app.verdict.model_classifier`).
- GREEN: `.venv/Scripts/python.exe -m pytest -q` -> 371 passed, the real-artifact integration test included (not skipped).
- Installed into `.venv`: `requirements-model.txt` (resolved to the same versions as `../sereno-model/requirements.txt`, transitive ones included) and `pip install --no-deps -e ../sereno-model`. `pyarrow` is required: `sereno_model.models.stacking` imports `sereno_model.data.split`, which imports it.
- Default `CLASSIFIER=fake`; `start.ps1` sets `CLASSIFIER=model` unless the caller set it. `MODEL_PATH` defaults to `../sereno-model/artifacts/2.0.0/model.joblib`.
- Run over HTTP (`CLASSIFIER=model`, spare port 5057, scratch `DATA_FILE`): `POST /v1/verdict {"url":"https://www.google.com/"}` -> `{"domain":"google.com","latencyMs":2336,"policy":"advertencia","score":0.0271...,"source":"modelo","verdict":"legitima"}`; a phishing-looking URL -> `verdict: "phishing"`, score 0.9896, 18 ms; the first URL again -> `source: "cache"`, 7 ms; `shop.pe` -> 400; `GET /v1/model` -> `{"version":"2.0.0"}`.
- Model load: about 13.5 s cold (first import of the ML libraries), about 2 s once the files are in the OS cache. It happens on the first verdict or `/v1/model` request, not at startup.

### Follow-up: invalidation row (parent decision, 2026-11-13)
- Decision: every invalidation row in the cache responses is `{domain, date, verdict}` with no `status`; `verdict` is `null` until a URL of the domain is evaluated again, then the model class. This replaces the T1 interpretation above; the backend emits no display text.
- Rows stored with a `status` text are upgraded on read (`"Reevaluado: ..."` to its class, anything else to `null`).
- RED: `pytest -q tests/test_cache.py tests/test_verdict.py` -> 8 failed, 97 passed. GREEN: `.venv/Scripts/python.exe -m pytest -q` -> 372 passed.
- `node --test tests/verdict-local-http.test.js` from `../sereno-extension` -> 1 passed, 0 failed.
- Commit `923bba2`. One of several full-suite runs around it failed on `tests/test_metrics.py::test_metrics_reflect_every_verdict_including_anonymous` (`['Modelo', 'Modelo', 'Caché']`): a flake that predates this work, the queries table ordered events sharing a clock instant arbitrarily. Fixed in the next commit with a deterministic test (RED: 2 failed in `tests/test_metrics.py`; GREEN: 373 passed, five consecutive full runs).

## Next step
T3: parent end-to-end check with the extension against `.\start.ps1`. The local `data/` files were not touched by this work; their legacy records upgrade on the first write.
