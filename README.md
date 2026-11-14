# Sereno authentication backend

Local Flask authentication, administrator domain policies, and SUS survey submission with anonymous response records plus separate account participation. Registration, login, server-side sessions, logout, administrator authorization, domain-policy storage, surveys, the verdict pipeline (cache, per-user history, metrics, model version) are real backend functionality. Data is stored in PostgreSQL and the verdict cache in Redis; there is no other storage, in the application or in the tests. Verdicts come from the trained Stacking model of the sibling `sereno-model` repository (artifact `2.0.0`) behind a classifier port; a deterministic stand-in (`FakeClassifier`) remains for tests and `CLASSIFIER=fake`.

## Run locally (PowerShell, Python 3.12)

From `sereno-backend/`, start the backend with the existing virtual environment:

```powershell
.\start.ps1
```

The launcher uses this folder's `.venv`, temporarily enables `AUTH_LOCAL_HTTP=1`, selects `CLASSIFIER=model` unless the caller already set `CLASSIFIER`, turns on the development tools (`DEV_TOOLS=1`, see below) unless the caller already set `DEV_TOOLS`, and binds to `127.0.0.1:5000` without the debugger or reloader. It first runs `docker compose up -d --wait`, which starts the PostgreSQL and Redis containers of `docker-compose.yml` if needed and waits until both are healthy; it stops with a clear message when Docker Desktop is not installed or not running, or when a service does not become healthy. The containers keep running after the backend stops (`docker compose stop` stops them, `docker compose down -v` also deletes the database volume). The launcher works from any caller directory when invoked by its path, restores the caller's directory and the settings it changed afterward, and preserves any existing `DATABASE_URL`, `REDIS_URL` and `MODEL_PATH` settings.

First-time setup only (Python 3.12 and Docker Desktop):

```powershell
python -m venv .venv
.venv/Scripts/python.exe -m pip install -r requirements.txt -r requirements-model.txt
.venv/Scripts/python.exe -m pip install --no-deps -e ../sereno-model
.\start.ps1
```

`requirements-model.txt` pins the runtime of the pickled model (numpy, pandas, scikit-learn, xgboost, lightgbm, joblib, scipy, tldextract, threadpoolctl, pyarrow) to exactly the versions artifact `2.0.0` was trained with in `../sereno-model/requirements.txt`; change both together. The editable `sereno-model` install is required in every mode, because the backend takes its URL normalizer from that package (that part needs only the standard library); `--no-deps` keeps the pins above in charge. To run without the ML libraries, skip `requirements-model.txt` and start with `$env:CLASSIFIER = 'fake'`.

`CLASSIFIER` is `model` or `fake`; any other value fails startup. Its default is `fake`, so the test suite and a bare `flask run` never load the artifact unless asked; `start.ps1` asks for `model`, and a non-test start with the fake logs a warning. `MODEL_PATH` defaults to `../sereno-model/artifacts/2.0.0/model.joblib` and resolves against this backend's root when relative. With `CLASSIFIER=model`, startup fails with an explanatory `ModelUnavailable` error if the artifact file or a model library is missing; the artifact itself is then preloaded in a background daemon thread started by `create_app`, so the server listens at once and the first verdict does not pay the load (about 13 s on a cold start on the development machine, 2 to 3 s afterwards; verdicts take tens of milliseconds). The log line `Model 2.0.0 loaded in N s` marks the model as ready. The load happens once, under a lock: a request that arrives while it is running waits for that same load instead of starting another. If the preload fails (for example a library version that does not match the pickle), a warning is logged, the process keeps running, and each later request retries the load and answers `500 {"error":"server"}` while it keeps failing. Nothing is preloaded and no thread starts with `CLASSIFIER=fake` or with an injected classifier that has no `warm_up()`.

Every data operation is a database transaction, so the data does not limit the number of processes, but this is still Flask's development server and each process would load its own copy of the model: run one. Debug mode is off by default; do not enable it or expose this development server publicly. Load the sibling `sereno-extension` unpacked, reload it, and register a throwaway account in its popup. Demo credentials are not backend accounts. Keep the address exactly `http://127.0.0.1:5000`.

`DATABASE_URL` (default `postgresql://sereno:sereno@127.0.0.1:5432/sereno`) names the PostgreSQL database and `REDIS_URL` (default `redis://127.0.0.1:6379/0`) the Redis database of the verdict cache; both defaults match `docker-compose.yml`. At startup the backend applies `app/storage/schema.sql` (idempotent: it only creates what is missing) and pings Redis; if either service is unreachable the start fails instead of serving errors. The compose file pins `postgres:17.6-alpine` and `redis:7.4-alpine`, publishes both ports on `127.0.0.1` only, keeps the database in the named volume `postgres-data` and runs Redis without persistence (the cache is disposable). Its credentials are for local development only. To change them, create a Git-ignored `.env` file beside `docker-compose.yml` with any of `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB` (all `sereno` by default), `POSTGRES_PORT` (`5432`), `REDIS_PORT` (`6379`), and the matching `DATABASE_URL` and `REDIS_URL` for the backend: Docker Compose reads the file by itself and `start.ps1` takes the two URLs from it; a manual `flask run` needs them in the environment.

Environments, caches and `.env` files are Git-ignored; no accounts are seeded.

### Development tools (`DEV_TOOLS`)

`DEV_TOOLS=1` adds a page to manage test accounts without touching the database: `http://127.0.0.1:5000/dev/users` lists the users and lets a developer change a role or delete an account. **It asks for no session, so it must never be enabled outside a developer machine.** It is off by default: without `DEV_TOOLS=1` (exactly `1`) none of its routes is registered and every `/dev/...` address is a plain 404. `start.ps1` turns it on for local runs; `$env:DEV_TOOLS = '0'; .\start.ps1` starts without it. It logs a warning when enabled.

| Endpoint | Success | Failure |
| --- | --- | --- |
| `GET /dev/users` | `200` self-contained HTML page (inline CSS and JavaScript, no external assets) | `404` unless direct loopback |
| `GET /dev/api/users` | `200 {"users":[{username, role, createdAt}]}` sorted by username | `404` as above |
| `PUT /dev/api/users/<username>/role` with `{"role":"user"\|"admin"}` | `200 {"user":{username, role, createdAt}}` | `400 {"error":"invalid"}`, `403 {"error":"forbidden"}`, `404 {"error":"not_found"}` |
| `DELETE /dev/api/users/<username>` | `200 {}` | `403 {"error":"forbidden"}`, `404 {"error":"not_found"}` |

Every route answers 404 unless the request is direct loopback: a loopback peer address, a literal loopback host (`127.0.0.1`, not a name that resolves to it, which also stops DNS rebinding) and no `Forwarded`/`X-Forwarded-*` header. The two state-changing routes also require the header `X-Sereno-Dev: 1`, and `PUT` requires `Content-Type: application/json`; anything else is `403`. A web page on another origin cannot send either without a CORS preflight, and the backend never answers one with an allowance (it sends no CORS header anywhere), so another site open in the same browser cannot change or delete accounts. The page itself is served with a restrictive `Content-Security-Policy`, cannot be framed, and writes usernames into the table as text, never as markup.

A role change applies to the sessions the account already has at their next request, because the server reloads the user on every request. Deleting an account is one database transaction: the account and its sessions are removed, its queries stay in `consulta` without a user (exactly like clearing the history, so the metrics keep counting them), and its survey participation mark is removed while the anonymous responses stay. The name can then be registered again and starts with no history and no participation. `AuthRepository` has `list_users`, `set_role` and `delete_user` for this; `DevUserService` (`app/dev/service.py`) is the use case, the page is `app/dev/page.py`, and the routes are `dev_routes` in `app/http.py`. Tests: `tests/test_dev_tools.py` and the account-administration cases of `tests/test_repository_contract.py`.

Public registration always creates a `user`, even if the request supplies a role. There is no promotion endpoint in the product API. To create an administrator for local development, use the development tools page above, or run `docker compose exec postgres psql -U sereno -d sereno -c "UPDATE usuario SET rol = 'admin' WHERE nombre = 'alice';"` (no restart needed). The only valid stored roles are `user` and `admin`; the database rejects any other value.

## HTTP contract

| Endpoint | Success | Failure |
| --- | --- | --- |
| `POST /v1/auth/register` | `201 {user, token}` | `400 {"error":"invalid"}`, `409 {"error":"duplicate"}` |
| `POST /v1/auth/login` | `200 {user, token}` | `401 {"error":"invalid"}` |
| `GET /v1/auth/session` | `200 {"session":null}` or `200 {session}` | Storage failure below |
| `POST /v1/auth/logout` | `200 {}`; revoke session and clear cookie | `400 {"error":"invalid"}` for malformed/nonobject JSON |
| `GET /v1/survey` | `200 {"submitted":true|false}` for the current account | `401 {"error":"invalid"}` without a valid session |
| `POST /v1/survey` | `200 {}` after the account's first durable submission | `400 {"error":"invalid"}`, `401 {"error":"invalid"}`, `409 {"error":"already_submitted"}` |
| `GET /v1/domains` | `200 {"domains":[...]}` for an administrator | `401 {"error":"invalid"}`, `403 {"error":"forbidden"}` |
| `PUT /v1/domains` | `200 {"domains":[...]}` after whole-list replacement | `400 {"error":"invalid"}`, `401 {"error":"invalid"}`, `403 {"error":"forbidden"}` |
| `POST /v1/verdict` | `200 {domain, verdict, policy, score, source, latencyMs}`; anonymous allowed | `400 {"error":"invalid"}`, `500 {"error":"server"}` (classifier), `503 {"error":"cache_down"}` |
| `GET /v1/history` | `200 {"entries":[{id, domain, verdict, policy, score, source, ts, latencyMs}]}` for the current account, newest first | `401 {"error":"invalid"}`, `500 {"error":"server"}` |
| `DELETE /v1/history` | `200 {}` after clearing the current account's history | `401 {"error":"invalid"}`, `500 {"error":"server"}` |
| `GET /v1/metrics` | `200 {"metrics":{...}}` in the dashboard shape, for an administrator | `401 {"error":"invalid"}`, `403 {"error":"forbidden"}`, `500 {"error":"server"}` |
| `GET /v1/model` | `200 {"version":"2.0.0"}`, the loaded model's version (`"1.3"` with the fake); no session needed | `500 {"error":"server"}` if the model cannot be loaded |
| `GET /v1/cache` | `200 {"cache":{ttlPhishingDays, ttlLegitHours, invalidations:[{domain, date, verdict}]}}` for an administrator | `401 {"error":"invalid"}`, `403 {"error":"forbidden"}`, `503 {"error":"cache_down"}` |
| `PUT /v1/cache/ttl` | `200 {"cache":{...}}` | `400 {"error":"invalid"}`, `401`, `403`, `503` as above |
| `POST /v1/cache/invalidations` | `200 {"cache":{...}}` with the new invalidation first | `400 {"error":"invalid"}`, `401`, `403`, `503` as above |

`POST /v1/survey` accepts exactly `{ "answers": [1,2,3,4,5,1,2,3,4,5] }`: ten JSON integers from 1 through 5. Booleans, floats, strings, extra fields, and other lengths are rejected without appending or consuming eligibility. Authentication and account ownership come only from the `sereno_session` cookie; client-supplied usernames are rejected. Each case-sensitive account may submit once. `GET /v1/survey` returns only whether that current account has participated, never answers or participant names.

Participation usernames are stored separately from the anonymous response records. Each response contains only validated answers and a server UTC `submittedAt` timestamp (`YYYY-MM-DDTHH:MM:SSZ`), with no username, token, IP address, participant reference, or identity-specific timestamp binding it to one participant. This separation avoids a direct identity field on responses, but it is not a claim of cryptographic anonymity. There is no survey-answer administration endpoint.

Domain-policy requests authenticate only with the current `sereno_session` cookie. The server reloads the user on every request, so manual promotion or demotion affects existing sessions immediately; client-supplied roles are never trusted. `PUT /v1/domains` accepts exactly `{ "domains": [{ "domain": "shop.example.com", "policy": "advertencia", "updatedAt": "2026-10-27" }] }` and atomically replaces the complete list. Domains are trimmed and lowercased, duplicates after normalization are rejected, policies are exactly `advertencia` or `bloqueo`, and dates are real calendar dates in `YYYY-MM-DD`. Invalid input does not change the stored list. These policies choose the response when a listed domain is detected as phishing; they do not classify domains. The default for an unlisted domain is `advertencia`.

`POST /v1/verdict` accepts exactly `{ "url": "https://shop.example.com/pay" }`. The URL is validated and normalized first with the RFC 3986 normalizer of the `sereno-model` package (lowercase scheme and host, default port and fragment dropped, unreserved percent-encodings decoded); anything that is not an `http(s)` URL with a host (`about:blank`, `shop.example.com`, `ftp://...`, text with spaces) is `400 invalid`. `domain` is the normalized host without a leading `www.`. `verdict` is the model class, `"phishing"` or `"legitima"`, with `score` the phishing probability. `policy` is the administrator's response policy for that domain, `"advertencia"` or `"bloqueo"`, always present and `"advertencia"` for an unlisted domain (a row typed as `www.<domain>` also applies): the backend does not apply it, the extension does, and only to a `phishing` verdict. The policy is read on every request and never cached, so a policy edit takes effect at once; if the policy store cannot be read the answer carries the default and the failure is logged. A fresh cached entry answers with `source: "cache"`; otherwise the injected classifier answers, the result is cached and `source` is `"modelo"`. `latencyMs` is the measured server-side evaluation time in whole milliseconds. For a signed-in session the verdict is also recorded in that account's history with the `verdict` and the `policy` in force when it was served (newest first, the same visit within 60 s replaced, 200 newest kept, `ts` in server local time `YYYY-MM-DDTHH:MM`); anonymous verdicts appear in no history, and a failure to record the verdict or to read the session never fails it. `DELETE /v1/history` does not delete rows: it unlinks the account from its queries, irreversibly, so the list is empty and the metrics keep counting those verdicts.

The classifier is a port (`app/verdict/classifier.py`: `classify(url) -> Classification(verdict, score)` plus `version`). `create_app(classifier=...)` injects one directly and wins over the `CLASSIFIER` setting. `ModelClassifier` (`app/verdict/model_classifier.py`) is the adapter over the `sereno-model` `StackingModel` loaded with joblib: `score` is `score_url(url)`, the verdict is `phishing` when the score reaches the model's own `threshold` (0.45 in artifact `2.0.0`), and `version` is its `model_version`. `warm_up()` loads the artifact ahead of the first use; the thread that calls it at startup lives in the composition root (`app/__init__.py`, `create_app(background=...)`), not in the adapter. Predictions run one at a time. `FakeClassifier` (`app/verdict/fake_classifier.py`) is a deterministic two-class stand-in over the extension's demo scenario domains: `tienda-servicios.pe` legitima 0.04, `paypal-secure-login.com` phishing 0.92, `pago-servicios-linea.net` phishing 0.97, anything else legitima 0.05, version `1.3`. The extension mock's `*.localhost` test hosts are client-side development aids and get the fallback.

`GET /v1/metrics` returns exactly the shape of the extension's `Sereno.seed.metrics`, computed at request time from a bounded log (newest 10,000) of every served verdict, anonymous ones included. The log stores `verdict` and `policy`; the three dashboard levels are derived from them (`legitima` is `seguro`, `phishing` takes its policy, `advertencia` or `bloqueo`). `analyzed`, `phishingCount`/`phishingPct` (`phishing` verdicts), mean `latencyMs` and `cacheHitPct` cover the last 24 hours; `deltaPct` compares with the 24 hours before (0 when there is no earlier traffic); `latencyTargetMs` is the fixed 200 ms goal; `hourly` holds 24 one-hour bars (dominant result, ties to the more severe; height scaled to 99 of the 110 px chart); each `sparklines` series holds 7 daily bars scaled to 32.4 of the 36 px strip (indigo count, red phishing, amber latency, green cache hit rate); `queries` lists the newest 50 with `DD/MM/YYYY HH:MM` server local stamps and `Caché`/`Modelo` labels. None of these fields needs the model's quality, so there are no placeholders. A failure to record a verdict never fails it. `GET /v1/model` reports `classifier.version` and is public, like the mock.

The verdict cache is administrator-only, with the same session/role checks as domain policies. `PUT /v1/cache/ttl` accepts exactly `{ "ttlPhishingDays": 7, "ttlLegitHours": 24 }`: JSON integers, 1-3650 days and 1-87600 hours (the defaults are the extension seed's 7 days and 24 hours). Each entry is keyed by the SHA-256 of the normalized URL and stores `{domain, verdict, score, cachedAt}`: the model class and the domain, never the URL and never the policy, so several entries may share a domain. `phishing` entries live `ttlPhishingDays`, `legitima` entries `ttlLegitHours`. `POST /v1/cache/invalidations` accepts exactly `{ "domain": "shop.example.com" }`, normalized like the extension mock's `domainOf` (URL hostname, lowercase, no leading `www.`); it drops every cached entry of that domain and prepends `{domain, date: "YYYY-MM-DD", verdict: null}` (server local date). Every invalidation row in the three cache responses is `{domain, date, verdict}`: `verdict` is `null` while the domain has not been evaluated again, and the model class (`"phishing"` or `"legitima"`) once a URL of that domain is next evaluated by the model. The backend emits no display text; the extension builds it from `verdict`. Cached entries are stored but never exposed. The entries live in Redis under that key, each written with the TTL of its class so Redis expires it by itself, and the TTL settings and the invalidation list live in PostgreSQL; a shortened TTL still applies at once to entries already cached, while a lengthened one only applies to entries cached afterwards. Redis or PostgreSQL being unreachable during a cache operation is `503 {"error":"cache_down"}`.

Note: the extension's remote adapter currently passes `forbidden` through only for domain-policy routes, so a non-administrator sees a generic `server` error on cache routes.

Registration/login accept JSON `{ "username": "alice", "password": "your-password" }`. Usernames are trimmed, nonempty, and case-sensitive; passwords must be nonempty strings and are not trimmed. Registration ignores requested roles and assigns `user`. Public users contain exactly `username`, `isAdmin`, `createdAt`; sessions contain exactly `username`, `isAdmin`, `loginAt`, `token`. `isAdmin` is a boolean; the stored role string is never exposed, and clients translate any role label themselves. Timestamps use UTC `YYYY-MM-DDTHH:MM`. Password hashes never appear in responses. Storage failures during requests return `500 {"error":"server"}` without internal details; PostgreSQL or Redis being unreachable at startup prevents serving.

### Cookie and browser boundary

The opaque token is returned for contract compatibility, but authentication uses only the `sereno_session` cookie, not an Authorization header. The cookie is host-only, path `/`, `Secure`, `HttpOnly`, and `SameSite=None`. Logout invalidates the server record; an old copied token cannot restore that session. Multiple logins create independent sessions; logout revokes only the presented token.

`AUTH_LOCAL_HTTP=1` explicitly opts into direct loopback development: cookies keep `HttpOnly` but use `SameSite=Lax` without `Secure`. All auth, survey, and domain-policy routes reject non-loopback peer addresses, non-loopback literal hosts, and any `Forwarded`/`X-Forwarded-*` headers with 403 before invoking the service. Do not put this mode behind a proxy. Values other than `1` leave secure defaults on; unset it for HTTPS (`Remove-Item Env:AUTH_LOCAL_HTTP`). No production cookie defaults are weakened.

The extension uses `credentials: "include"` and a host permission for HTTP `127.0.0.1`; no CORS or `cookies` API permission is needed for privileged requests from extension pages. Chrome documents that host-permitted extension requests are treated as same-site, subject to browser cookie settings. Real Chrome cookie acceptance/restoration remains unverified; isolated HTTP tests do not prove browser behavior. For nonlocal use, supply trusted HTTPS and explicit deployment hardening, not this development flag.

References: [Chrome network requests](https://developer.chrome.com/docs/extensions/develop/concepts/network-requests), [Chrome cookies](https://developer.chrome.com/docs/extensions/develop/concepts/storage-and-cookies), [Flask cookie security](https://flask.palletsprojects.com/en/stable/web-security/), [proxy trust](https://flask.palletsprojects.com/en/stable/deploying/proxy_fix/).

## Architecture and storage limits

Callers/transports and persistence can vary; authentication rules and the HTTP contract must remain stable. This small hexagonal boundary isolates those two changes without a generic repository framework:

- `app/http.py` checks JSON structure and field types, then constructs immutable `Credentials`. It never passes request dictionaries into use cases.
- `Credentials` enforces nonempty, UTF-8-encodable strings and trims usernames while preserving passwords. These invariants apply to non-HTTP callers too. `AuthService.register(credentials)` and `login(credentials)` consume this typed input; existing response dictionaries remain unchanged.
- `AuthService` depends on the structural `AuthRepository` Protocol and injected hash, verification, clock, and token functions. Domain/application code imports no Flask, Werkzeug, filesystem, SQL, or persistence adapters.
- The composition root `app/__init__.py` takes the PostgreSQL and Redis adapters from `app/storage/factory.py` and injects Werkzeug's default hash/verification functions.

The repository contract assumes valid domain records. Registration atomically creates a user and that user's initial session, returning `False` without changes for duplicate usernames. Missing lookups return `None`; revocation is idempotent and affects only the given token. Invalid session references raise `StorageError` without changes. Records are independent snapshots on input/output. Multiple sessions are supported; writing an existing token replaces its session, so the caller must supply unique tokens. The PostgreSQL adapter runs each operation as one database transaction, atomic across threads and processes.

The survey domain/service depend only on the survey repository Protocol, not Flask or SQL. `PostgresSurveyRepository` runs the duplicate check, the participant mark, and the anonymous response insert in one transaction, so a failed submission does not consume eligibility.

The policy domain and service similarly import no Flask or SQL code. `PostgresPolicyRepository` replaces the whole list in one transaction under a table lock, so readers always see one complete list; a storage failure returns a controlled server error and keeps the previous list.

The verdict (`app/verdict/`), history (`app/history/`) and metrics (`app/metrics/`) modules use the same layering: pure domain rules, a repository Protocol, a PostgreSQL adapter, and a service (with an injected clock for the metrics and, for verdicts, an injected classifier, timer, journal and policy lookup). The history and query-log ports only read: the verdict journal is the single writer of served verdicts. `app/verdict/url.py` is the only place that touches the `sereno-model` normalizer.

The cache module (`app/cache/`) follows the same split: the cached entry, the administration view (`CacheState`: TTL settings and invalidations) and their rules in `domain.py`, the `CacheRepository` port (`settings`, `set_ttl`, `invalidate`, `lookup`, `store`: one key at a time, as a key-value store serves it) with `RedisCacheRepository`, and `CacheService` with an injectable clock.

The database holds password hashes and bearer session tokens, and which accounts answered the survey (apart from the responses): restrict access to it and do not commit, share, or expose a dump of it.

### PostgreSQL and Redis

Every port has a database adapter (`postgres_repository.py` in each module, `app/cache/redis_repository.py` for the cache), all built on `app/storage/postgres.py`: a thread-safe connection pool where each repository operation is one transaction, so operations are atomic across threads and processes, and any driver failure is raised as the module's own storage exception (the HTTP mapping above is unchanged). No ORM and no migration framework: `app/storage/schema.sql` is the schema.

The six tables of the architecture document:

| Table | Columns |
| --- | --- |
| `usuario` | `id_usuario`, `nombre` (unique, case-sensitive), `hash_contrasena`, `rol` (`user`, `admin`), `fecha_registro` |
| `url_evaluada` | `id_url`, `hash_url` (SHA-256 of the normalized URL, unique), `dominio` |
| `consulta` | `id_consulta`, `id_usuario` (optional), `id_url`, `veredicto`, `politica`, `puntaje`, `origen` (`HIT`, `MISS`), `latencia_ms`, `fecha` |
| `prediccion` | `id_prediccion`, `id_consulta` (unique), `veredicto`, `puntaje`, `codigo_version`, `fecha` |
| `politica_dominio` | `id_politica`, `dominio` (unique), `politica`, `fecha_actualizacion` |
| `version_modelo` | `codigo` (key), `fecha`, `reporte_metricas` |

Auxiliary tables for data the document gives no table for: `sesion` (`token`, `id_usuario`, `fecha_inicio`), `encuesta_respuesta` (`id_respuesta`, `respuestas`, `fecha_envio`) and `encuesta_participante` (`id_usuario`), which share no column so a response cannot be traced to its participant, `configuracion_cache` (one row: `ttl_phishing_dias`, `ttl_legitima_horas`) and `invalidacion_cache` (`id_invalidacion`, `dominio`, `fecha`, `veredicto`).

No table, Redis key or log line holds a URL: only its hash and its domain (RNF-05). Each served verdict is one `consulta` row, written with its `url_evaluada` row in a single transaction through the verdict journal (`app/verdict/journal.py`, `app/verdict/postgres_journal.py`). The history and the metrics are two readings of those rows: the history lists the rows that still belong to the account, hiding a row when a later one repeats the visit within 60 s and showing the newest 200; the metrics read the newest 10,000 rows, anonymous ones included. `consulta` keeps the `politica` and the `puntaje` that were answered. A `MISS` also gets a `prediccion` row with what the model said, tied to `version_modelo`, where the running model's version is registered the first time it answers (`fecha` is that moment; `reporte_metricas` stays empty, the backend has no report to store). A `HIT` has no `prediccion`. History record ids are `h-<id_consulta>`.

In Redis an entry is a string under the bare hash of its URL holding `{domain, verdict, score, cachedAt}`, and a set `dominio:<domain>` lists the keys of each host, so an invalidation deletes exactly those keys without scanning the keyspace.

Not covered: there is no backup or migration tooling beyond the idempotent schema file, the pool and timeouts are sized for one local machine, and the credentials in `docker-compose.yml` are development defaults.

This is not production-hardened: session expiry, CSRF protection, rate limiting, account recovery, production server/TLS deployment, and browser-origin controls remain future work. Sessions persist until logout or manual offline removal; do not use this slice for real accounts.

## Verify

```powershell
.venv/Scripts/python.exe -m pytest -q
.venv/Scripts/python.exe -m pytest -q tests/test_repository_contract.py tests/test_service.py
.venv/Scripts/python.exe -m compileall -q app
.venv/Scripts/python.exe -m pytest -q tests/test_auth.py -k smoke
```

The suite needs both services: start them with `docker compose up -d --wait`. Without them the run stops at once with that instruction (exit code 1) instead of skipping tests. The tests never touch development data: they use the database `sereno_test` (created on first use, its schema rebuilt once per run, its tables emptied before each test; `TEST_DATABASE_URL` overrides it and must name a database ending in `_test`) and Redis database 15 (`TEST_REDIS_URL`, never index 0, flushed around each test). `tests/conftest.py` holds those fixtures, the `config` fixture every HTTP test builds its application with, and the helpers that read the stored rows or change a role.

`tests/test_repository_contract.py` covers the account port, including concurrent duplicate registration, reference integrity, snapshot isolation and account administration. `tests/test_policy_survey_contract.py` covers the policy and survey ports, `tests/test_consulta.py` the `consulta` model (journal, history and query log), `tests/test_cache.py` the cache port and its routes, and `tests/test_redis_cache.py` what is specific to Redis: key TTLs, the domain index and either store being down. `tests/test_app.py` walks the HTTP contract end to end and checks that no URL reaches a table or a Redis key; `tests/test_storage_setup.py` covers the settings, the compose file, the schema and the service check of the suite. `tests/test_service.py` uses deterministic hash/verification/clock/token fakes without Flask clients. HTTP behavior, application recreation and error regressions are in `tests/test_auth.py`, `tests/test_survey.py`, `tests/test_survey_participation.py`, `tests/test_policies.py`, `tests/test_history.py`, `tests/test_metrics.py` and `tests/test_verdict.py`, using Flask's test client with injected clocks, timers and classifiers where needed. Storage failures are provoked with an unreachable database (the `database_down` fixture) or a failing adapter operation. `tests/test_model_classifier.py` covers the model adapter and the `CLASSIFIER`/`MODEL_PATH` settings with a stub model, plus one integration test that loads the real artifact over HTTP; that test is skipped when the artifact or the model libraries are absent, and it is the only one that loads them. These are not browser integration tests.
