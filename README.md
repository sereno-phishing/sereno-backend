# Sereno authentication backend

Local Flask authentication with replaceable JSON persistence. Registration, login, server-side sessions, and logout connect to the sibling extension; its history, verdicts, administration, and ML remain mocked.

## Run locally (PowerShell, Python 3.12)

From `sereno-backend/`:

```powershell
python -m venv .venv
.venv/Scripts/python.exe -m pip install -r requirements.txt
$env:STORAGE_DRIVER = "json"
$env:DATA_FILE = "data/db.json"
$env:AUTH_LOCAL_HTTP = "1"
.venv/Scripts/python.exe -m flask --app app:create_app run --host 127.0.0.1 --port 5000 --no-debugger --no-reload
```

Use one process only. Debug mode is off by default; do not enable it or expose this development server publicly. Load the sibling `sereno-extension` unpacked, reload it, and register a throwaway account in its popup. Demo credentials are not backend accounts. Keep the address exactly `http://127.0.0.1:5000`.

`STORAGE_DRIVER` defaults to `json`; any other value fails startup. `DATA_FILE` defaults to `data/db.json`. Relative paths always resolve against this backend's root, not the invocation directory. An absent file is initialized with empty `users` and `sessions` objects. Invalid existing data fails closed and is never automatically reset. Fix or restore damaged files manually while the server is stopped. Runtime data, environments, and caches are Git-ignored; no accounts are seeded.

## HTTP contract

| Endpoint | Success | Failure |
| --- | --- | --- |
| `POST /v1/auth/register` | `201 {user, token}` | `400 {"error":"invalid"}`, `409 {"error":"duplicate"}` |
| `POST /v1/auth/login` | `200 {user, token}` | `401 {"error":"invalid"}` |
| `GET /v1/auth/session` | `200 {"session":null}` or `200 {session}` | Storage failure below |
| `POST /v1/auth/logout` | `200 {}`; revoke session and clear cookie | `400 {"error":"invalid"}` for malformed/nonobject JSON |

Registration/login accept JSON `{ "username": "alice", "password": "your-password" }`. Usernames are trimmed, nonempty, and case-sensitive; passwords must be nonempty strings and are not trimmed. Registration ignores requested roles and assigns `usuario`. Public users contain exactly `username`, `role`, `createdAt`; sessions contain exactly `username`, `role`, `loginAt`, `token`. Timestamps use UTC `YYYY-MM-DDTHH:MM`. Password hashes never appear in responses. Storage failures during requests return `500 {"error":"server"}` without file paths; startup storage failures prevent serving.

### Cookie and browser boundary

The opaque token is returned for contract compatibility, but authentication uses only the `sereno_session` cookie, not an Authorization header. The cookie is host-only, path `/`, `Secure`, `HttpOnly`, and `SameSite=None`. Logout invalidates the server record; an old copied token cannot restore that session. Multiple logins create independent sessions; logout revokes only the presented token.

`AUTH_LOCAL_HTTP=1` explicitly opts into direct loopback development: cookies keep `HttpOnly` but use `SameSite=Lax` without `Secure`. All auth routes reject non-loopback peer addresses, non-loopback literal hosts, and any `Forwarded`/`X-Forwarded-*` headers with 403 before invoking the service. Do not put this mode behind a proxy. Values other than `1` leave secure defaults on; unset it for HTTPS (`Remove-Item Env:AUTH_LOCAL_HTTP`). No production cookie defaults are weakened.

The extension uses `credentials: "include"` and a host permission for HTTP `127.0.0.1`; no CORS or `cookies` API permission is needed for privileged requests from extension pages. Chrome documents that host-permitted extension requests are treated as same-site, subject to browser cookie settings. Real Chrome cookie acceptance/restoration remains unverified; isolated HTTP tests do not prove browser behavior. For nonlocal use, supply trusted HTTPS and explicit deployment hardening, not this development flag.

References: [Chrome network requests](https://developer.chrome.com/docs/extensions/develop/concepts/network-requests), [Chrome cookies](https://developer.chrome.com/docs/extensions/develop/concepts/storage-and-cookies), [Flask cookie security](https://flask.palletsprojects.com/en/stable/web-security/), [proxy trust](https://flask.palletsprojects.com/en/stable/deploying/proxy_fix/).

## Architecture and storage limits

Callers/transports and persistence can vary; authentication rules and the HTTP contract must remain stable. This small hexagonal boundary isolates those two changes without a generic repository framework:

- `app/http.py` checks JSON structure and field types, then constructs immutable `Credentials`. It never passes request dictionaries into use cases.
- `Credentials` enforces nonempty, UTF-8-encodable strings and trims usernames while preserving passwords. These invariants apply to non-HTTP callers too. `AuthService.register(credentials)` and `login(credentials)` consume this typed input; existing response dictionaries remain unchanged.
- `AuthService` depends on the structural `AuthRepository` Protocol and injected hash, verification, clock, and token functions. Domain/application code imports no Flask, Werkzeug, filesystem, SQL, or persistence adapters.
- The composition root `app/__init__.py` selects JSON and injects Werkzeug's default hash/verification functions. `MemoryAuthRepository` is an independent, process-local test implementation, not a selectable runtime driver; `STORAGE_DRIVER=memory` remains unsupported.

The repository contract assumes valid domain records. Registration atomically creates a user and that user's initial session, returning `False` without changes for duplicate usernames. Missing lookups return `None`; revocation is idempotent and affects only the given token. Invalid session references raise `StorageError` without changes. Records are independent snapshots on input/output. Multiple sessions are supported; writing an existing token replaces its session, so the caller must supply unique tokens. Atomicity is process-local, not a distributed transaction guarantee.

`JsonAuthRepository` validates the complete schema, serializes operations using a process-wide lock per resolved file, and writes through a temporary sibling plus `os.replace`. Registration creates the user and initial session atomically. This does not coordinate multiple processes, external writers, network filesystems, or multiple hosts. The file contains password hashes and bearer session tokens: restrict local filesystem access and do not commit or share it.

A future database requires a new repository adapter, composition-root selection, contract tests, and explicit data migration; changing an environment variable alone is not enough. No database adapter is implemented now.

This is not production-hardened: session expiry, CSRF protection, rate limiting, account recovery, production server/TLS deployment, and browser-origin controls remain future work. Sessions persist until logout or manual offline removal; do not use this slice for real accounts.

## Verify

```powershell
.venv/Scripts/python.exe -m pytest -q
.venv/Scripts/python.exe -m pytest -q tests/test_repository_contract.py tests/test_service.py
.venv/Scripts/python.exe -m compileall -q app
.venv/Scripts/python.exe -m pytest -q tests/test_auth.py -k smoke
```

`tests/test_repository_contract.py` runs the same parametrized behavior suite against JSON and memory, including concurrent duplicate registration, reference integrity, and snapshot isolation. `tests/test_service.py` uses memory plus deterministic hash/verification/clock/token fakes, without Flask clients or real JSON storage. HTTP, corruption preservation, atomic replacement, application recreation, and error regressions remain in `tests/test_auth.py` using isolated temporary files and Flask's test client. These are not browser integration tests.
