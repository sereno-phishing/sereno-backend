# Sereno authentication backend

Local Flask authentication with replaceable JSON persistence. This development slice implements registration, login, server-side sessions, and logout only. The extension is unchanged; history, verdicts, administration, and ML are not implemented.

## Run locally (PowerShell, Python 3.12)

From `sereno-backend/`:

```powershell
python -m venv .venv
.venv/Scripts/python.exe -m pip install -r requirements.txt
$env:STORAGE_DRIVER = "json"
$env:DATA_FILE = "data/db.json"
.venv/Scripts/python.exe -m flask --app app:create_app run --host 127.0.0.1 --port 5000 --no-debugger --no-reload
```

Use one process only. Debug mode is off by default; do not enable it or expose this development server publicly. The HTTP command is useful for test clients and diagnostics, but browser cookies require the HTTPS guidance below.

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

For browser development, terminate trusted local HTTPS in front of this localhost server. A plain HTTP localhost launch is not a guarantee that Chrome will accept or send Secure cookies: browser/version exceptions, extension origins, third-party-cookie policies, and credentialed requests still need real integration verification. No Chrome integration has been tested here. No CORS is configured: authorize the exact extension origin before adding credentialed CORS; never use a wildcard origin with credentials.

## Architecture and storage limits

Persistence is the varying concern; the HTTP contract and application rules must remain stable. `app/http.py` calls `AuthService`, which depends on the `AuthRepository` Protocol. Domain/application code imports no Flask, Werkzeug, filesystem, or SQL. The composition root `app/__init__.py` alone selects the JSON implementation and injects Werkzeug's default password hash/verification functions. This small port/adapter boundary costs a few files but isolates the real storage change without a generic repository framework.

`JsonAuthRepository` validates the complete schema, serializes operations using a process-wide lock per resolved file, and writes through a temporary sibling plus `os.replace`. Registration creates the user and initial session atomically. This does not coordinate multiple processes, external writers, network filesystems, or multiple hosts. The file contains password hashes and bearer session tokens: restrict local filesystem access and do not commit or share it.

A future database requires a new repository adapter, composition-root selection, contract tests, and explicit data migration; changing an environment variable alone is not enough. No database adapter is implemented now.

This is not production-hardened: session expiry, CSRF protection, rate limiting, account recovery, production server/TLS deployment, and browser-origin controls remain future work. Sessions persist until logout or manual offline removal; do not use this slice for real accounts.

## Verify

```powershell
.venv/Scripts/python.exe -m pytest -q
.venv/Scripts/python.exe -m compileall -q app
.venv/Scripts/python.exe -m pytest -q tests/test_auth.py -k smoke
```

Tests use isolated temporary files and Flask's test client, including application recreation and logout revocation. They are not browser integration tests.
