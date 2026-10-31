# Verdict pipeline with a stub classifier

## Objective
Implement every REST route the extension still serves from its mock (`/v1/verdict`, `/v1/history`, `/v1/cache*`, `/v1/metrics`, `/v1/model`) so the extension can switch `provider` to `"remote"`. The ML model stays out: verdicts come from a deterministic `FakeClassifier` behind a `Classifier` port, so plugging the Stacking model later means writing one adapter.

## Problem and rationale
The backend serves auth, domain policies and the SUS survey only. `config.provider` in the extension moves verdict, history, cache, metrics and model together, so it cannot go remote until all of them exist. The model is the longest piece of work; everything around it is built first.

## Authorized scope
- sereno-backend: new modules (cache, verdict with classifier port and fake adapter, history, metrics/model) with JSON/in-memory repositories like auth/survey/policy; routes in `app/http.py`; config paths; tests; README.
- sereno-extension: switch `provider` to `"remote"`, adjust README, config and tests that assumed mock verdict data.
- No ML model, no Redis, no remote operations, no academic documents.

## Constraints
English technical artifacts. Strict TDD (pytest / node --test), RED before GREEN. REST contract = "Contrato REST" table in `sereno-extension/README.md`; shapes must match the extension mock. Cache is a JSON/in-memory repository behind a port (Redis later). Commits: Conventional Commits, no AI attribution.

## Work units
- [x] T1 Backend cache: repository port + JSON/memory adapters, TTL and invalidations, `GET /v1/cache`, `PUT /v1/cache/ttl`, `POST /v1/cache/invalidations`. Route: delegated (multi-file write).
- [ ] T2 Backend verdict: `Classifier` port + `FakeClassifier`, cache lookup by TTL, `POST /v1/verdict`, history recorded for signed-in users. Route: delegated.
- [ ] T3 Backend history: `GET/DELETE /v1/history` per user. Route: delegated.
- [ ] T4 Backend metrics and model: `GET /v1/metrics` computed from stored data, `GET /v1/model` reports the classifier version. Route: delegated.
- [ ] T5 Extension: `provider: "remote"`, README/tests updated, end-to-end local HTTP test. Route: delegated.

## Progress and evidence
- T1: RED `pytest tests/test_cache.py` -> collection error (no `app.cache`). GREEN full suite 273 passed (229 + 44). Cache routes admin-only (401/403 like domains); storage failure 503 `cache_down`. Invalidation date ISO `YYYY-MM-DD` (seed format; the mock writes `DD/MM/YYYY`, the UI accepts both). Pending invalidations become `Reevaluado: <status>` on the next model evaluation (seed shows that state). Default invalidations empty (seed rows are demo data).

## Next step
T2.
