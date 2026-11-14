# Verdicts during a cache outage (RF-20)

## Objective
Keep serving verdicts when the cache is unavailable: requirement RF-20 of the approved design, with its acceptance case CP-12 ("stop Redis and request a verdict: the request is resolved with the model, without interrupting the service, and the incident is written to the log").

## Problem and rationale
With Redis unreachable, `POST /v1/verdict` answered `503 {"error":"cache_down"}`: the cache raised `CacheStorageError` and the verdict blueprint turned it into that response. The cache is an optimisation; the verdict is the protection itself, like the policy lookup and the journal, which already never fail it.

## Decisions
- Where: `VerdictService.evaluate` catches `CacheStorageError` around the lookup and around the store. The cache port and its Redis adapter keep raising it, so the three `/v1/cache` routes still answer `503 cache_down`. The verdict blueprint lost its `cache_down` handler.
- Any `CacheStorageError` falls back, not only a stopped Redis: an unreadable entry and the TTL settings in PostgreSQL out of reach also mean the cache cannot be used.
- `source` is `"modelo"`: it is the truth, and the extension already knows the value, so the response contract does not change.
- Record: `consulta.origen` `MISS` with its `prediccion` row and model version, like any other model answer. No new `origen` value and no schema change (`CHECK (origen IN ('HIT','MISS'))` stays). The metrics need nothing: `cacheHitPct` counts those rows as non-hits, so it drops while the outage lasts, which is what happened.
- Log: one `WARNING` per request from `app.verdict.service`, with the failed operation (`read`, `write` or `paused`), the domain, the URL hash and the cause; never the URL (RNF-05).
- After a failed read the entry is not written: one wait per request, not two.
- Pause: a stopped compose Redis takes about 4 s to fail (the name does not resolve; `socket_connect_timeout` does not bound that), and the extension waits 4 s for a verdict. After a failure the verdict route skips the cache for `CACHE_PAUSE_SECONDS` (10 s) and answers straight from the model, so one request per pause waits. Measured in the container: 3.98 s for the first request, 15 ms for the next ones.
- Startup is unchanged: the server still refuses to start without Redis (`app/storage/factory.py`).

## Scope
- `app/verdict/service.py`, `app/http.py`, `tests/`, `README.md`, this file.
- Not in scope: other routes, the startup check, the Redis client settings (`app/storage/`), the Dockerfile and compose files, the extension, landing and model repositories.

## Constraints
English technical artifacts. Conventional commits, no AI attribution. The `postgres-data` volume and the development database are never removed or reset. No push, no merge.

## Work units
- [x] F1 Fallback (route: single writer, inline): tests first, then the service and the blueprint. Commits `ec7d797` (tests), `3973853`.
- [x] F2 README: the verdict failure modes. Commit `f24e8af`.
- [x] F3 Pause after a failure, found by the container check: tests first, then the service and the README. Commits `3885d83`, `542b642`, `8f39e7a` (test fix).
- [x] F4 Functional check CP-12 in the containers, the full suite and the extension suite.

## Checks
`.venv/Scripts/python.exe -m pytest -q` (408 passed on the base `2a716f6`). `node --test` in `..\sereno-extension` (512 pass on the base). `docker compose --profile server up -d --build --wait`, `docker compose stop redis`, `POST /v1/verdict`, `docker compose logs server`, `docker compose start redis`, the same new URL twice.

## Progress and evidence
Branch `feat/cache-outage-fallback` from `2a716f6` (`feat/server-container`).

- RED for F1: 6 failed, 88 passed in the three touched test files (read failure, write failure, classifier failure during an outage, PostgreSQL down, Redis down over HTTP, recovery). GREEN: 416 passed.
- Old expectations replaced on purpose: `tests/test_app.py` `test_redis_being_down_reports_cache_down_and_spares_the_rest` (503) is now `test_redis_being_down_still_serves_the_verdict_with_the_model` (200, `modelo`, `MISS`, one warning without the URL); `tests/test_verdict.py` `test_an_unreachable_database_reports_cache_down` (503) is now `test_an_unreachable_database_still_serves_the_verdict`. `tests/test_redis_cache.py::test_redis_being_down_is_a_cache_storage_error` is kept: it is the contract of the cache port, which the fallback relies on. `tests/test_cache.py` now checks `503 cache_down` on each of the three cache routes.
- RED for F3: `CACHE_PAUSE_SECONDS` missing (collection error). The first GREEN commit (`3885d83`) went in with one failing test, a test defect (it read the warning of the earlier request too); fixed in `8f39e7a`.
- Full suite: `.venv/Scripts/python.exe -m pytest -q` -> 417 passed, nothing skipped.
- Extension suite, not edited: `node --test` -> 512 tests, 511 pass, 1 fail. The failing test is `tests/verdict-local-http.test.js` "remote verdicts, history, cache, metrics and model version run against the real backend": lines 137 and 140 expect `{ ok: false, error: "cache_down" }` from a verdict after corrupting the Redis entries and after renaming `configuracion_cache`; both are now verdicts from the model. Line 139 (`admin.data.getCache()` -> `cache_down`) still holds. One of three runs also had two cleanup timeouts (`spawnSync ... ETIMEDOUT` in `tests/local-backend.js`), not reproduced in the next run.
- CP-12 in the containers (image rebuilt from `8f39e7a`, real model 2.0.0): Redis stopped -> `200`, `source: "modelo"`, 3.98 s, then 15 ms for the next two requests; the server log has one line per request, `WARNING in service: Cache unavailable (read); verdict served by the model (domain=..., urlHash=...): Error -2 connecting to redis:6379. Name or service not known.` and then `Cache unavailable (paused)`; Redis started and healthy, 11 s later a new URL -> `modelo` (12 ms) then `cache` (2 ms), with no new warning: the same client reconnected. `consulta`: three `MISS` with `prediccion` 2.0.0 for the outage, then `MISS` and `HIT`. The three services ended running and healthy.

## Open points
- The extension test above must be updated in `sereno-extension` (out of scope here), and its README still lists `503 cache_down` for `POST /v1/verdict`. The extension keeps the `cache_down` reason for the cache routes.
- One request per pause still waits about 4 s against a stopped compose Redis, the extension's own limit, so that request can end as `timeout` there. The Redis client is built in `app/storage/factory.py`, outside this change; a shorter wait needs a decision there or a background probe.
- An unreadable entry pauses the cache for every URL for 10 s and is not rewritten until it expires or its domain is invalidated; each request for it is answered by the model with a warning.
- The checks left nine anonymous `consulta` rows in the development database, domains `cp12-outage-*.example.net` and `cp12-recovery-*.example.net`.

## Next step
Andrés: review the branch, decide the merge order with `feat/server-container`, and update the extension contract test and README.
