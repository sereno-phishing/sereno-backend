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
- [x] T1: Implement and verify register, login, session and logout with replaceable JSON persistence and a documented local runtime.
  - Route: delegated direct; preparation and multi-file implementation trigger delegation.
  - Acceptance: existing extension auth response shapes; persistence across application recreation; duplicate/invalid credentials; logout revocation; password hashes only; controlled/atomic writes; storage selection fails closed; independent repository contract coverage.
  - Checks: `.venv/Scripts/python.exe -m pytest -q`; `.venv/Scripts/python.exe -m compileall -q app`; HTTP smoke using Flask test client; independent verification if native risk is high/unavailable.
  - Observed proof: initial import RED; behavior RED for invalid logout JSON (4 failed); first implementation 45 passed. Independent verification reproduced unencodable credentials and corrupt-hash error failures; correction RED 4 failed, 47 passed; final GREEN 51 passed. Compileall exit 0; HTTP smoke 1 passed, 50 deselected. Independent final verification: 6 focused regression cases and all 51 tests passed. Runtime data and environment files are ignored.
  - Commit: 2797477ba130cd2152fc3eff32579ba7bff32fd6, feat(auth): add json-backed authentication api. Initial implementation committed locally with tests and documentation. Independent repository contract acceptance was initially pending and is now satisfied by the verified T2 commit below.

- [x] T2: Decouple authentication inputs from HTTP and prove repository interchangeability.
  - Authorized by the user after the initial commits. Scope: typed application inputs, HTTP mapping, in-memory repository, shared repository contract tests, service tests without Flask/filesystem, and documentation. No new endpoints, database driver, extension changes, or production-hardening expansion.
  - Route: delegated direct; reading for implementation and multiple non-trivial files require one writer.
  - Acceptance: AuthService consumes typed inputs rather than request dictionaries; HTTP responses and validation remain compatible; identical repository contract tests pass against JSON and memory; application flows run with memory and injected hashing/clock/token functions without Flask or disk. Default storage remains JSON.
  - Strict TDD runner: `.venv/Scripts/python.exe -m pytest -q`. Observe failing deterministic tests before implementation, then GREEN; no test removals to conceal regressions.
  - Checks: full pytest; focused `tests/test_repository_contract.py tests/test_service.py`; compileall; existing HTTP smoke; native read-only risk assessment and applicable independent verification. Parent repeats one reported command.
  - Observed proof: baseline 51 passed; deterministic RED 10 failed, 59 passed, 11 errors; first GREEN 80 passed; final 85 passed. Shared contracts and service tests: 34 passed, including 20 cases across both repositories. Compileall exit 0; HTTP smoke 1 passed, 50 deselected; diff check clean. Parent repeated all 34 focused tests successfully. Independent verifier repeated full 85, focused 34, smoke, and diff checks with no candidate-caused defects found.
  - Status: implemented, verified and committed as 3aa951a983047645402d9945e57356de801b0e0c, refactor(auth): isolate inputs and verify repository contracts. Source change: 322 additions plus 32 deletions (354 authored lines), excluding the task document. JSON registration now rejects an initial session belonging to another user, discovered by the shared contract suite. No API responses or default driver changed.

## Delivery strategy
ask-on-risk; user selected feature-branch-chain. Initial forecast approximately 380 authored lines; observed implementation is 653 authored lines, excluding generated environment, runtime data, and this task document. One slicing pass found no cohesive smaller deliverable with existing files intact: HTTP, persistence and regression tests share one suite. Keep the initial auth unit together on feat/json-auth; do not create artificial branches. Before any future PR, resolve its size through authorized cohesive slices or explicit size:exception acceptance. No push or PR authorized. No code was compressed or tests removed to meet a size target.

T2 forecast: approximately 300 authored additions plus deletions, advisory only; do not compress code, omit tests, or split artificially to meet it. Child branch feat/auth-boundaries starts from fbc9807 on feat/json-auth. The chain strategy is already chosen; no push, PR or merge is authorized.

T2 observed source change: 354 authored lines. Accumulated feature source change: 1007 authored additions plus deletions across work units; this is not a claim about a future PR diff. Preserve feature-branch-chain and resolve review slices before any PR; no remote delivery is authorized.

## Verification and review
RDD is off, decided by global preference (read before implementation). Native assessment returned high/unassessable because new files are untracked. No native review started. Independent verification and the parent rerun were performed: 45 passed before correction; independent final suite 51 passed. Parent final smoke rerun: 1 passed, 50 deselected. No fabricated native approval.

Pre-commit verification repeated: 51 passed; compileall exit 0; smoke 1 passed, 50 deselected. Candidate files have no trailing whitespace or conflict markers. Generated environment, data and local secrets files are ignored. Runtime proof uses Flask test clients, not a real browser.

T2: RDD remains off (global). Native risk assessment returned high (authentication hot path); independent verification completed as described above. No native review was started and no native approval is claimed.

## Progress
Created a new local Git repository on feat/json-auth. Installed Flask 3.1.3, Werkzeug 3.1.9 and pytest 8.4.2 in isolated .venv. Implemented registration, login, session lookup and logout behind injected repository and hashing boundaries. Default data/db.json initializes on first application creation, with no seeded accounts. Atomic JSON replacement and shared per-path process locks are covered. Extension remains on mock and was not edited. No PostgreSQL, history, verdict/ML, CORS, real Chrome integration, session expiry, CSRF protection or rate limiting is implemented; this is a local-development auth slice, not production-ready.

T2 implements immutable typed credentials with transport-independent invariants, HTTP mapping, an independent memory adapter, shared repository contracts and framework-free service flows with injected functions. The architectural source-import guard reads source files; functional service flows do not use filesystem persistence. Memory is available through injection for tests, not as a configured runtime driver.

## Recovery mirror
Engram topic: odd/json-auth/tasks, project: tesis-phishing, locator: sereno-backend/odd/tasks/json-auth.md. Use the established thesis project for the backend-scoped recovery copy. New project writes were refused as unknown_project; no memory configuration was changed.

## Next step
T1 and T2 outcomes and checks are complete. Parent feat/json-auth ends at fbc9807; child feat/auth-boundaries contains T2 beginning with 3aa951a. Further API features and extension/browser integration require separate authorization. Production-hardening and real-browser checks remain out of scope; no push, merge or pull request was performed.
