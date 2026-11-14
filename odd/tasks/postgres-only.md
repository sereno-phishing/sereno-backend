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
- [ ] U1 Storage removal: the JSON and memory adapters, `app/json_file.py`, `app/legacy.py`, the import tool and its `importacion` table, `STORAGE_DRIVER`, `DATA_FILE` and the `*_DATA_FILE` settings, the driver table of the factory; every test re-pointed to the PostgreSQL test database and the Redis test index; fail-fast check of the services; local data files deleted.
- [ ] U2 Launcher and documentation: `start.ps1`, `docker-compose.yml` comment, README.
- [ ] U3 Dead code the removal exposes: write paths that only the file storage used (history and query-log appends, the non-journal branch of the verdict service), whole-state cache helpers, unused port methods.

## Checks
`.venv/Scripts/python.exe -m pytest -q` with the containers up (537 passed on the base `cc1091b`) and with them stopped (one failure message, no skips). Search for leftovers: `rg -n -i "json_repository|memory_repository|json_file|STORAGE_DRIVER|DATA_FILE|import_json|legacy" app tests README.md start.ps1`. HTTP check on port 5059 with `CLASSIFIER=fake` against a throwaway database and Redis index.

## Progress and evidence
Branch `refactor/postgres-only` from `cc1091b`.

## Next step
U1.
