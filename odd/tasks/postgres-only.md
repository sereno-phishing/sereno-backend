# PostgreSQL and Redis as the only storage

## Objective
Remove the JSON storage and the in-memory storage, and everything that existed only for them. PostgreSQL (relational data) and Redis (verdict cache) are the only storage, in the application and in the tests.

## Decision
Andrés, 2026-11-13: PostgreSQL and Redis are the only storage. The local JSON data files under `data/` are deleted at his request. The REST contract does not change.

## Scope
- `sereno-backend` only: `app/`, `tests/`, `data/`, `requirements.txt`, `docker-compose.yml`, `.gitignore`, `start.ps1`, `README.md`, this file.
- Not in scope: the sibling `sereno-extension` repository (two of its contract tests start this backend on the JSON driver and are fixed in a later task), the model code, `requirements-model.txt`, the academic documents.
- The `fake` classifier and the `CLASSIFIER` setting stay: they are the deterministic stand-in of this suite and of the extension's contract tests.

## Constraints
English technical artifacts; table and column names in Spanish. Conventional commits, no AI attribution. Every commit leaves the suite green. Tests never touch the development database `sereno` or Redis index 0. With the containers down the suite fails once, fast, with the command that starts them; it never skips.

## Work units
- [x] U1 Storage removal (route: single writer, multi-file): the JSON and memory adapters, `app/json_file.py`, `app/legacy.py`, the import tool and its `importacion` table, `STORAGE_DRIVER`, `DATA_FILE` and the `*_DATA_FILE` settings, the driver table of the factory; every test re-pointed to the PostgreSQL test database and the Redis test index; fail-fast check of the services; local data files deleted. Commit `b1149b6`.
- [x] U3 Dead code the removal exposed: the verdict journal is the single writer, so the verdict service requires it and the history and query-log ports only read (their appends, `HistoryRepository.all`, the history record builder and the in-memory dedupe rule are gone); `url_evaluada.hash_url` and `consulta.puntaje` are NOT NULL; whole-state cache helpers, `SurveyRepository.list_submissions` and the default-policy option of `PolicyRow.create` removed. Commit `9cea253`.
- [x] U2 Launcher and documentation: `start.ps1` without a driver, `docker-compose.yml` comment, README. Commit `603909f`.

## Checks
`.venv/Scripts/python.exe -m pytest -q` with the containers up (537 passed on the base `cc1091b`) and with them stopped (one failure message, no skips). Search for leftovers: `rg -n -i "json_repository|memory_repository|json_file|STORAGE_DRIVER|DATA_FILE|import_json|legacy" app tests README.md start.ps1`. HTTP check on port 5059 with `CLASSIFIER=fake` against a throwaway database and Redis index.

## Progress and evidence
Branch `refactor/postgres-only` from `cc1091b`.

- Fail-fast check, test first. RED: `tests/test_storage_setup.py::test_unreachable_services_are_named_with_the_command_that_starts_them` failed with `ImportError: cannot import name 'services_down_message' from 'conftest'`. GREEN after `tests/conftest.py` gained `unreachable_services`, `services_down_message` and `pytest_sessionstart`.
- Re-pointed tests keep their assertions; file checks became row checks (`rows(config, table...)`), manual role edits became `set_role(config, username, role)`, and storage failures are provoked with the `database_down` fixture (no pool connection can be obtained) or by making one adapter operation raise its storage error. Deleted as specific to the removed storage: file corruption, atomic replace, old record shapes, driver selection, path resolution, JSON-only capacity and recreation tests. The suite went from 537 to 405 tests after U1 and to 400 after U3.
- Final run, containers up: `.venv/Scripts/python.exe -m pytest -q` -> 400 passed in 61 s, exit code 0, nothing skipped.
- Containers stopped (`docker compose stop`): exit code 1 in 5 s with `Exit: PostgreSQL and Redis unreachable. The tests need both services: run 'docker compose up -d --wait' in the backend folder, then run the tests again.` Started again with `docker compose up -d --wait`: both healthy. Redis runs without persistence, so the development cache (index 0) was emptied by the stop.
- Leftover search: no match in `app`, `tests`, `README.md`, `start.ps1`.
- Files (`fd -H -t f . -E .git -E .venv -E __pycache__ | wc -l`, ignored files not counted): 93 before, 80 after (14 tracked files deleted, this document added). The 8 untracked files of `data/` and the directory were deleted too.
- HTTP check, port 5059, `CLASSIFIER=fake`, throwaway database `sereno_e2e` and Redis index 14 (database dropped, index flushed, server process tree stopped): `GET /v1/model` -> `{"version":"1.3"}`; register -> 201; the same URL twice -> `source: "modelo"` then `source: "cache"` (phishing, 0.97, policy `advertencia`); history -> one entry (`cache`, `h-2`); rows: usuario 1, consulta 2, prediccion 1, url_evaluada 1; Redis keys: the URL hash and `dominio:pago-servicios-linea.net`.

## Open points
- The development database `sereno` was created with the earlier schema: it keeps an unused `importacion` table and the looser `hash_url`/`puntaje` columns (the schema file only creates what is missing). The code works with it. To drop the table: `docker compose exec postgres psql -U sereno -d sereno -c "DROP TABLE IF EXISTS importacion;"`.
- `sereno-extension`: `tests/verdict-local-http.test.js` and `tests/auth-local-http.test.js` still start this backend with `DATA_FILE` and `STORAGE_DRIVER: 'json'`; they need an isolated PostgreSQL database and Redis index (later task).

## Next step
Andrés: review the branch and decide the merge. Then the extension task above.
