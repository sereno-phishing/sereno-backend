# JSON-backed authentication API

## Objective
Provide the extension's existing authentication HTTP contract with replaceable persistence, initially a local JSON file.

## Problem and rationale
Browser-local mock data cannot act as a shared backend database. Keep HTTP, business rules, and storage separate so changing persistence does not require frontend or use-case changes.

## Authorized scope
Create sereno-backend alongside sereno-extension. Implement Flask authentication/session endpoints, repository ports and JSON adapter, configuration, tests, and setup documentation. No extension edits, ML, PostgreSQL implementation, remote operations, or academic document changes.

## Constraints
English technical artifacts. Strict TDD enabled by global instructions: observe RED, GREEN, then refactor with pytest. Hash passwords behind an injected boundary. Keep runtime JSON and credentials out of Git. JSON is single-process development storage, not a production database. Preserve corrupt data rather than silently resetting it. No AI commit attribution.

## Work units
- [ ] T1: Implement and verify register, login, session and logout with replaceable JSON persistence and a documented local runtime.
  - Route: delegated direct; preparation and multi-file implementation trigger delegation.
  - Acceptance: existing extension auth response shapes; persistence across application recreation; duplicate/invalid credentials; logout revocation; password hashes only; controlled/atomic writes; storage selection fails closed; independent repository contract coverage.
  - Checks: `.venv/Scripts/python.exe -m pytest -q`; `.venv/Scripts/python.exe -m compileall -q app`; HTTP smoke using Flask test client; independent verification if native risk is high/unavailable.
  - Observed proof: initial import RED; behavior RED for invalid logout JSON (4 failed); first implementation 45 passed. Independent verification reproduced unencodable credentials and corrupt-hash error failures; correction RED 4 failed, 47 passed; final GREEN 51 passed. Compileall exit 0; HTTP smoke 1 passed, 50 deselected. Independent final verification: 6 focused regression cases and all 51 tests passed. Runtime data and environment files are ignored.
  - Commit: initial verified implementation ready for commit; identity will be recorded immediately afterward. Repository behavior is tested against JSON; a separate in-memory implementation and shared contract suite remain unimplemented, so full architecture acceptance is still pending.

## Delivery strategy
ask-on-risk; user selected feature-branch-chain. Initial forecast approximately 380 authored lines; observed implementation is 653 authored lines, excluding generated environment, runtime data, and this task document. One slicing pass found no cohesive smaller deliverable with existing files intact: HTTP, persistence and regression tests share one suite. Keep the initial auth unit together on feat/json-auth; do not create artificial branches. Before any future PR, resolve its size through authorized cohesive slices or explicit size:exception acceptance. No push or PR authorized. No code was compressed or tests removed to meet a size target.

## Verification and review
RDD is off, decided by global preference (read before implementation). Native assessment returned high/unassessable because new files are untracked. No native review started. Independent verification and the parent rerun were performed: 45 passed before correction; independent final suite 51 passed. Parent final smoke rerun: 1 passed, 50 deselected. No fabricated native approval.

Pre-commit verification repeated: 51 passed; compileall exit 0; smoke 1 passed, 50 deselected. Candidate files have no trailing whitespace or conflict markers. Generated environment, data and local secrets files are ignored. Runtime proof uses Flask test clients, not a real browser.

## Progress
Created a new local Git repository on feat/json-auth. Installed Flask 3.1.3, Werkzeug 3.1.9 and pytest 8.4.2 in isolated .venv. Implemented registration, login, session lookup and logout behind injected repository and hashing boundaries. Default data/db.json initializes on first application creation, with no seeded accounts. Atomic JSON replacement and shared per-path process locks are covered. Extension remains on mock and was not edited. No PostgreSQL, history, verdict/ML, CORS, real Chrome integration, session expiry, CSRF protection or rate limiting is implemented; this is a local-development auth slice, not production-ready.

## Recovery mirror
Engram topic: odd/json-auth/tasks, project: tesis-phishing, locator: sereno-backend/odd/tasks/json-auth.md. Use the established thesis project for the backend-scoped recovery copy. New project writes were refused as unknown_project; no memory configuration was changed.

## Next step
Commit the verified initial auth unit and record its identity. Pending architecture proposals: typed use-case inputs independent of the request dictionary, plus a shared repository contract tested against JSON and an in-memory adapter. These refinements are not included in this commit request. Keep the extension unchanged; no push or pull requests.
