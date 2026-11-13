# Sereno authentication backend

Local Flask authentication, administrator domain policies, and SUS survey submission with anonymous response records plus separate account participation. Registration, login, server-side sessions, logout, administrator authorization, domain-policy storage, surveys, the verdict pipeline (cache, per-user history, metrics, model version) are real backend functionality. Verdicts come from the trained Stacking model of the sibling `sereno-model` repository (artifact `2.0.0`) behind a classifier port; a deterministic stand-in (`FakeClassifier`) remains for tests and `CLASSIFIER=fake`.

## Run locally (PowerShell, Python 3.12)

From `sereno-backend/`, start the backend with the existing virtual environment:

```powershell
.\start.ps1
```

The launcher uses this folder's `.venv`, temporarily enables `AUTH_LOCAL_HTTP=1`, selects `CLASSIFIER=model` unless the caller already set `CLASSIFIER`, and binds to `127.0.0.1:5000` without the debugger or reloader. It works from any caller directory when invoked by its path, restores the caller's directory, local HTTP and classifier settings afterward, and preserves any existing `STORAGE_DRIVER`, `DATA_FILE` and `MODEL_PATH` settings.

First-time setup only (Python 3.12):

```powershell
python -m venv .venv
.venv/Scripts/python.exe -m pip install -r requirements.txt -r requirements-model.txt
.venv/Scripts/python.exe -m pip install --no-deps -e ../sereno-model
.\start.ps1
```

`requirements-model.txt` pins the runtime of the pickled model (numpy, pandas, scikit-learn, xgboost, lightgbm, joblib, scipy, tldextract, threadpoolctl, pyarrow) to exactly the versions artifact `2.0.0` was trained with in `../sereno-model/requirements.txt`; change both together. The editable `sereno-model` install is required in every mode, because the backend takes its URL normalizer from that package (that part needs only the standard library); `--no-deps` keeps the pins above in charge. To run without the ML libraries, skip `requirements-model.txt` and start with `$env:CLASSIFIER = 'fake'`.

`CLASSIFIER` is `model` or `fake`; any other value fails startup. Its default is `fake`, so the test suite and a bare `flask run` never load the artifact unless asked; `start.ps1` asks for `model`, and a non-test start with the fake logs a warning. `MODEL_PATH` defaults to `../sereno-model/artifacts/2.0.0/model.joblib` and, like the data files, resolves against this backend's root when relative. With `CLASSIFIER=model`, startup fails with an explanatory `ModelUnavailable` error if the artifact file or a model library is missing; the artifact itself is loaded lazily on the first verdict or `GET /v1/model` request (about 13 s on a cold start on the development machine, a few seconds afterwards; later verdicts take tens of milliseconds), once, under a lock. If loading fails then (for example a library version that does not match the pickle), the request answers `500 {"error":"server"}`, the reason is logged, and the next request retries.

Use one process only. Debug mode is off by default; do not enable it or expose this development server publicly. Load the sibling `sereno-extension` unpacked, reload it, and register a throwaway account in its popup. Demo credentials are not backend accounts. Keep the address exactly `http://127.0.0.1:5000`.

`STORAGE_DRIVER` defaults to `json`; any other value fails startup. `DATA_FILE` defaults to `data/db.json`. `SURVEY_DATA_FILE`, `POLICY_DATA_FILE`, `CACHE_DATA_FILE`, `HISTORY_DATA_FILE` and `QUERY_LOG_DATA_FILE` are optional; by default they use `surveys.json`, `policies.json`, `cache.json`, `history.json` and `queries.json` beside the resolved `DATA_FILE`. Relative paths always resolve against this backend's root, not the invocation directory. Absent files are initialized with empty auth, survey, or policy collections. Invalid existing data fails closed and is never automatically reset. Fix or restore damaged files manually while the server is stopped. Runtime data, environments, and caches are Git-ignored; no accounts are seeded.

Public registration always creates a `user`, even if the request supplies a role. There is no promotion endpoint. To create an administrator for local development, stop the server, back up the auth JSON file, change that user's exact `role` value from `user` to `admin`, and restart. The only valid stored roles are `user` and `admin`; the legacy Spanish values (`usuario`, `administrador`) and all other values make the auth file fail closed, so migrate any existing auth file by hand. Never edit runtime JSON while the server is running.

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
| `GET /v1/cache` | `200 {"cache":{ttlPhishingDays, ttlLegitHours, invalidations}}` for an administrator | `401 {"error":"invalid"}`, `403 {"error":"forbidden"}`, `503 {"error":"cache_down"}` |
| `PUT /v1/cache/ttl` | `200 {"cache":{...}}` | `400 {"error":"invalid"}`, `401`, `403`, `503` as above |
| `POST /v1/cache/invalidations` | `200 {"cache":{...}}` with the new invalidation first | `400 {"error":"invalid"}`, `401`, `403`, `503` as above |

`POST /v1/survey` accepts exactly `{ "answers": [1,2,3,4,5,1,2,3,4,5] }`: ten JSON integers from 1 through 5. Booleans, floats, strings, extra fields, and other lengths are rejected without appending or consuming eligibility. Authentication and account ownership come only from the `sereno_session` cookie; client-supplied usernames are rejected. Each case-sensitive account may submit once. `GET /v1/survey` returns only whether that current account has participated, never answers or participant names.

Participation usernames are stored separately from the anonymous response records. Each response contains only validated answers and a server UTC `submittedAt` timestamp (`YYYY-MM-DDTHH:MM:SSZ`), with no username, token, IP address, participant reference, or identity-specific timestamp binding it to one participant. This separation avoids a direct identity field on responses, but it is not a claim of cryptographic anonymity. There is no survey-answer administration endpoint.

Domain-policy requests authenticate only with the current `sereno_session` cookie. The server reloads the user on every request, so manual promotion or demotion affects existing sessions immediately; client-supplied roles are never trusted. `PUT /v1/domains` accepts exactly `{ "domains": [{ "domain": "shop.example.com", "policy": "advertencia", "updatedAt": "2026-10-27" }] }` and atomically replaces the complete list. Domains are trimmed and lowercased, duplicates after normalization are rejected, policies are exactly `advertencia` or `bloqueo`, and dates are real calendar dates in `YYYY-MM-DD`. Invalid input does not change the stored file. These policies choose the response when a listed domain is detected as phishing; they do not classify domains. The default for an unlisted domain is `advertencia`.

`POST /v1/verdict` accepts exactly `{ "url": "https://shop.example.com/pay" }`. The URL is validated and normalized first with the RFC 3986 normalizer of the `sereno-model` package (lowercase scheme and host, default port and fragment dropped, unreserved percent-encodings decoded); anything that is not an `http(s)` URL with a host (`about:blank`, `shop.example.com`, `ftp://...`, text with spaces) is `400 invalid`. `domain` is the normalized host without a leading `www.`. `verdict` is the model class, `"phishing"` or `"legitima"`, with `score` the phishing probability. `policy` is the administrator's response policy for that domain, `"advertencia"` or `"bloqueo"`, always present and `"advertencia"` for an unlisted domain (a row typed as `www.<domain>` also applies): the backend does not apply it, the extension does, and only to a `phishing` verdict. The policy is read on every request and never cached, so a policy edit takes effect at once; if the policy store cannot be read the answer carries the default and the failure is logged. A fresh cached entry answers with `source: "cache"`; otherwise the injected classifier answers, the result is cached and `source` is `"modelo"`. `latencyMs` is the measured server-side evaluation time in whole milliseconds. For a signed-in session the verdict is also recorded in that account's history with the `verdict` and the `policy` in force when it was served (newest first, the same visit within 60 s replaced, 200 newest kept, `ts` in server local time `YYYY-MM-DDTHH:MM`); anonymous verdicts are not recorded, and a history or session storage failure never fails the verdict.

The classifier is a port (`app/verdict/classifier.py`: `classify(url) -> Classification(verdict, score)` plus `version`). `create_app(classifier=...)` injects one directly and wins over the `CLASSIFIER` setting. `ModelClassifier` (`app/verdict/model_classifier.py`) is the adapter over the `sereno-model` `StackingModel` loaded with joblib: `score` is `score_url(url)`, the verdict is `phishing` when the score reaches the model's own `threshold` (0.45 in artifact `2.0.0`), and `version` is its `model_version`. Predictions run one at a time. `FakeClassifier` (`app/verdict/fake_classifier.py`) is a deterministic two-class stand-in over the extension's demo scenario domains: `tienda-servicios.pe` legitima 0.04, `paypal-secure-login.com` phishing 0.92, `pago-servicios-linea.net` phishing 0.97, anything else legitima 0.05, version `1.3`. The extension mock's `*.localhost` test hosts are client-side development aids and get the fallback.

`GET /v1/metrics` returns exactly the shape of the extension's `Sereno.seed.metrics`, computed at request time from a bounded log (newest 10,000) of every served verdict, anonymous ones included. The log stores `verdict` and `policy`; the three dashboard levels are derived from them (`legitima` is `seguro`, `phishing` takes its policy, `advertencia` or `bloqueo`). `analyzed`, `phishingCount`/`phishingPct` (`phishing` verdicts), mean `latencyMs` and `cacheHitPct` cover the last 24 hours; `deltaPct` compares with the 24 hours before (0 when there is no earlier traffic); `latencyTargetMs` is the fixed 200 ms goal; `hourly` holds 24 one-hour bars (dominant result, ties to the more severe; height scaled to 99 of the 110 px chart); each `sparklines` series holds 7 daily bars scaled to 32.4 of the 36 px strip (indigo count, red phishing, amber latency, green cache hit rate); `queries` lists the newest 50 with `DD/MM/YYYY HH:MM` server local stamps and `Caché`/`Modelo` labels. None of these fields needs the model's quality, so there are no placeholders. A query-log failure never fails a verdict. `GET /v1/model` reports `classifier.version` and is public, like the mock.

The verdict cache is administrator-only, with the same session/role checks as domain policies. `PUT /v1/cache/ttl` accepts exactly `{ "ttlPhishingDays": 7, "ttlLegitHours": 24 }`: JSON integers, 1-3650 days and 1-87600 hours (the defaults are the extension seed's 7 days and 24 hours). Each entry is keyed by the SHA-256 of the normalized URL and stores `{domain, verdict, score, cachedAt}`: the model class and the domain, never the URL and never the policy, so several entries may share a domain. `phishing` entries live `ttlPhishingDays`, `legitima` entries `ttlLegitHours`; a changed TTL applies to entries already cached. `POST /v1/cache/invalidations` accepts exactly `{ "domain": "shop.example.com" }`, normalized like the extension mock's `domainOf` (URL hostname, lowercase, no leading `www.`); it drops every cached entry of that domain and prepends `{domain, status: "Se reevaluará en la próxima consulta", date: "YYYY-MM-DD"}` (server local date). When a URL of the domain is next evaluated by the model, its pending invalidations become `"Reevaluado: <verdict>"`. Cached entries are stored but never exposed.

Files written before the two-class model stay readable: history and query-log records with the former three-state `status` are read as `seguro` = `legitima`/`advertencia`, `advertencia` = `phishing`/`advertencia`, `bloqueo` = `phishing`/`bloqueo`, and cache entries keyed by domain are dropped (they cannot be re-keyed by URL). Each file is rewritten in the current shape on its next update. Cache storage failures return `503 {"error":"cache_down"}`. Note: the extension's remote adapter currently passes `forbidden` through only for domain-policy routes, so a non-administrator sees a generic `server` error on cache routes.

Registration/login accept JSON `{ "username": "alice", "password": "your-password" }`. Usernames are trimmed, nonempty, and case-sensitive; passwords must be nonempty strings and are not trimmed. Registration ignores requested roles and assigns `user`. Public users contain exactly `username`, `isAdmin`, `createdAt`; sessions contain exactly `username`, `isAdmin`, `loginAt`, `token`. `isAdmin` is a boolean; the stored role string is never exposed, and clients translate any role label themselves. Timestamps use UTC `YYYY-MM-DDTHH:MM`. Password hashes never appear in responses. Storage failures during requests return `500 {"error":"server"}` without file paths; startup storage failures prevent serving.

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
- The composition root `app/__init__.py` selects JSON and injects Werkzeug's default hash/verification functions. `MemoryAuthRepository` is an independent, process-local test implementation, not a selectable runtime driver; `STORAGE_DRIVER=memory` remains unsupported.

The repository contract assumes valid domain records. Registration atomically creates a user and that user's initial session, returning `False` without changes for duplicate usernames. Missing lookups return `None`; revocation is idempotent and affects only the given token. Invalid session references raise `StorageError` without changes. Records are independent snapshots on input/output. Multiple sessions are supported; writing an existing token replaces its session, so the caller must supply unique tokens. Atomicity is process-local, not a distributed transaction guarantee.

`JsonAuthRepository` validates the complete auth schema, serializes operations using a process-wide lock per resolved file, and writes through a temporary sibling plus `os.replace`. Registration creates the user and initial session atomically. `JsonSurveyRepository` uses the same locked atomic-write pattern for a separate `{surveys, participants}` schema; its domain/service depend only on the survey repository Protocol, not Flask or JSON. The duplicate check, participant mark, and anonymous response append occur under one lock and one replacement, so a failed replacement does not consume eligibility. Participant names are sorted to avoid encoding submission order beside response order.

The policy domain and service similarly import no Flask or filesystem code. `JsonPolicyRepository` owns the strict `{domains:[...]}` schema, the per-resolved-path lock, and temporary-sibling/fsync/replace writes. Corrupt policy data prevents startup or returns a controlled server error if corruption occurs while running; it is never reset silently.

The verdict (`app/verdict/`), history (`app/history/`) and metrics (`app/metrics/`) modules use the same layering: pure domain rules, a repository Protocol, a JSON adapter, and a service with injected clock (and, for verdicts, an injected classifier, timer and policy lookup). `app/verdict/url.py` is the only place that touches the `sereno-model` normalizer; `app/legacy.py` upgrades records stored with the former three-state `status`. The history file stores usernames with visited domains and the query log stores visited domains: treat both like the auth file.

The cache module (`app/cache/`) follows the same split: `CacheState` and its rules in `domain.py`, the `CacheRepository` port (`read`, atomic `update`) with `JsonCacheRepository` and the in-memory `MemoryCacheRepository`, and `CacheService` with an injectable clock. A Redis adapter would replace only the repository. The newer JSON adapters share `app/json_file.py` (per-path lock, temporary sibling, fsync, replace, fail-closed validation).

Legacy exact `{surveys:[...]}` files remain readable and are not eagerly rewritten. Missing `participants` means no account has yet been recorded under this policy; the file upgrades only after a successful new submission, preserving all earlier anonymous responses. Malformed participation data fails closed and is preserved rather than reset.

These JSON adapters are single-process development storage only, not a production database. They do not coordinate multiple processes, external writers, network filesystems, or multiple hosts. The auth file contains password hashes and bearer session tokens, while the survey file contains participant usernames separately from response records: restrict local filesystem access and do not commit, share, or expose either file.

A future database requires a new repository adapter, composition-root selection, contract tests, and explicit data migration; changing an environment variable alone is not enough. No database adapter is implemented now.

This is not production-hardened: session expiry, CSRF protection, rate limiting, account recovery, production server/TLS deployment, and browser-origin controls remain future work. Sessions persist until logout or manual offline removal; do not use this slice for real accounts.

## Verify

```powershell
.venv/Scripts/python.exe -m pytest -q
.venv/Scripts/python.exe -m pytest -q tests/test_repository_contract.py tests/test_service.py
.venv/Scripts/python.exe -m compileall -q app
.venv/Scripts/python.exe -m pytest -q tests/test_auth.py -k smoke
```

`tests/test_repository_contract.py` runs the same parametrized behavior suite against JSON and memory, including concurrent duplicate registration, reference integrity, and snapshot isolation. `tests/test_service.py` uses memory plus deterministic hash/verification/clock/token fakes, without Flask clients or real JSON storage. HTTP, corruption preservation, atomic replacement, application recreation, and error regressions remain in `tests/test_auth.py` using isolated temporary files and Flask's test client. `tests/test_survey.py` and `tests/test_survey_participation.py` cover validation, one-response participation, status restoration, legacy files, storage recovery, and concurrent in-process app/repository instances. `tests/test_cache.py`, `tests/test_verdict.py`, `tests/test_history.py` and `tests/test_metrics.py` cover the verdict pipeline with injected clocks, timers and classifiers. `tests/test_model_classifier.py` covers the model adapter and the `CLASSIFIER`/`MODEL_PATH` settings with a stub model, plus one integration test that loads the real artifact over HTTP; that test is skipped when the artifact or the model libraries are absent, and it is the only one that loads them. These are not browser integration tests.
