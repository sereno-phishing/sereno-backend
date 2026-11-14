# PostgreSQL and Redis storage

## Objective
Persist the backend in PostgreSQL (relational data) and Redis (verdict cache), as the architecture document (ADD, OE2) defines, behind the existing repository ports. The JSON and memory adapters stay for tests and offline use.

## Problem and rationale
Every module persists to JSON files under one in-process lock. The ADD defines six PostgreSQL tables (`usuario`, `consulta`, `url_evaluada`, `prediccion`, `politica_dominio`, `version_modelo`) and a Redis cache keyed by the hash of the evaluated URL with a TTL per class (7 days phishing, 24 hours legitimate, configurable by the administrator, RF-21). RNF-05: the relational database keeps only the hash and the domain of a URL, never the URL; nothing writes the URL to logs.

## Decisions
- Local infrastructure: Docker Compose (PostgreSQL and Redis containers); Docker Desktop is installed, nothing native.
- Clearing a user's history unlinks the user from their `consulta` rows (user set to null, irreversible) instead of deleting the rows, so the dashboard metrics keep counting them (Andrés, 2026-11-13). This departs from the literal text of RF-16 and is recorded as an inconsistency for the ADD.
- History and dashboard metrics both come from `consulta` (RF-12, RF-14). `prediccion` holds the score and the model version of each MISS; a HIT has no `prediccion` row.
- Data the ADD gives no table for gets auxiliary PostgreSQL tables: server sessions, SUS survey responses and participants (kept unlinkable, as today), cache TTL settings and the invalidation list. Recorded as an inconsistency for the ADD.
- Redis holds the cache entries only. The value keeps the score and the domain besides the verdict and the date, because the response needs the score on a HIT and invalidation is per domain. Recorded as an inconsistency against RNF-05's wording.
- Driver: psycopg 3 with plain SQL and redis-py. No ORM, no migration framework: one idempotent schema file.
- The REST contract does not change. The browser extension is not touched.

## Authorized scope
- sereno-backend only: `app/`, `tests/`, `requirements*.txt`, `docker-compose.yml`, `.env.example`, `.gitignore`, `start.ps1`, `README.md`, this file.
- Local containers only. No remote operations, no academic documents, no change in `sereno-model` or `sereno-extension`.

## Constraints
English technical artifacts; table and column names in Spanish as the ADD names them. Test first (pytest), RED before GREEN. Conventional commits, no AI attribution. The suite must pass with the services down (database tests skip) and with them up. Tests never touch the development database or Redis database 0.

## Work units
- [x] T1 Infrastructure and composition: `docker-compose.yml`, schema, connection settings (`STORAGE_DRIVER=postgres`, `DATABASE_URL`, `REDIS_URL`), adapter factory in the composition root, test fixtures for both services. Route: delegated (multi-file write).
- [x] T2 PostgreSQL adapters for accounts, sessions, domain policies and the SUS survey, under shared contract tests. Route: delegated.
- [x] T3 `consulta` model: history and query log on `url_evaluada`, `consulta`, `prediccion` and `version_modelo`; history clear unlinks the user. Route: delegated.
- [ ] T4 Redis cache adapter with per-class TTL and per-domain invalidation; TTL settings and invalidations in PostgreSQL; `cache_down` preserved. Route: delegated.
- [ ] T5 One-off import of the local JSON data into PostgreSQL, `start.ps1` on the new stores, README. Route: delegated.
- [ ] T6 Local end-to-end check over HTTP with the real model on PostgreSQL and Redis. Route: parent spot check.

## Checks
`.venv/Scripts/python.exe -m pytest -q` (380 passed before this work), with the containers down and with them up.

## Progress and evidence
Branch `feat/postgres-redis-storage` from `366152e`.

- T1 (delegated writer), commit `27456c0`. `docker-compose.yml` (postgres:17.6-alpine, redis:7.4-alpine, loopback ports, healthchecks, named volume), `app/storage/schema.sql` (the six ADD tables plus six auxiliary ones, idempotent, applied under an advisory lock), `app/storage/postgres.py` (pooled `Database`, one transaction per operation, driver failures raised as the caller's storage exception), `app/storage/factory.py` (driver table used by the composition root), settings `DATABASE_URL` and `REDIS_URL`, fixtures in `tests/conftest.py` (database `sereno_test`, Redis index 15, skip when unreachable). RED: `tests/test_storage_setup.py` failed at collection with `ModuleNotFoundError: No module named 'app.storage'`. GREEN: `.venv/Scripts/python.exe -m pytest -q` -> 387 passed (containers up). `STORAGE_DRIVER=postgres` becomes selectable in T4, when every port has its adapter. `.env.example` was not written: the session's permission settings deny writing `.env*` files; its intended content is in the README.
- T2 (delegated writer), commit `3cf0747`. `app/auth/postgres_repository.py` (`usuario`, `sesion`; registration and its first session in one transaction, the unique name decides a concurrent race), `app/policy/postgres_repository.py` (`politica_dominio`, whole-list replacement under a table lock, order kept), `app/survey/postgres_repository.py` (`encuesta_respuesta` and `encuesta_participante` share no column; duplicate check, participant mark and response in one transaction; the participant must be an existing account). Contract tests: `tests/test_repository_contract.py` now runs json, memory and postgres; `tests/test_policy_survey_contract.py` runs the policy and survey ports against json and postgres. RED: 25 errors and 2 failures (`ModuleNotFoundError` for the three adapters), JSON cases green. GREEN: `.venv/Scripts/python.exe -m pytest -q` -> 429 passed (containers up).
- T3 (delegated writer). `app/verdict/journal.py` (`VerdictJournal` port, `ServedVerdict` with the URL hash and the model version) and the optional `journal` of `VerdictService`: with it, one record per served verdict replaces the history and query-log writes; without it (JSON driver) nothing changes. `app/verdict/postgres_journal.py` writes `url_evaluada`, `consulta` and, on a MISS, `version_modelo` and `prediccion` in one transaction. `app/history/postgres_repository.py` reads a user's `consulta` rows applying the 60 s dedupe and the 200 cap as listing rules, and `clear` sets `id_usuario` to NULL. `app/metrics/postgres_repository.py` reads the newest 10,000 rows. Decisions: `consulta` carries `politica` and `puntaje` (the values answered; a HIT has no `prediccion` to take the score from); records that arrive through the history and query-log ports (import, tests) have no URL hash, so they hang from a `url_evaluada` row with `hash_url` NULL (one per domain) and get no `prediccion`; history ids are `h-<id_consulta>`. RED: `tests/test_consulta.py` -> 4 failed, 12 errors (`ModuleNotFoundError`), 7 passed (JSON cases). GREEN: `.venv/Scripts/python.exe -m pytest -q` -> 452 passed (containers up).

## Next step
T4.
