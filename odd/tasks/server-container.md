# Evaluation server container

## Objective
Run the Flask evaluation server in a container, so that the three containers of the approved design (inference server, Redis, PostgreSQL) exist and can be brought up and demonstrated locally with the real model (artifact 2.0.0).

## Problem and rationale
Only PostgreSQL and Redis run in Docker; the server runs on the host through `start.ps1` and Flask's development server. The thesis describes a three-container environment and needs evidence that it comes up and serves verdicts.

## Decisions
- WSGI server: gunicorn, pinned in `requirements.txt` with a `sys_platform != "win32"` marker (it cannot run on Windows, so the host virtual environment does not install it). One worker with threads by default, like the host flow: each worker loads its own copy of the model, and with one worker a healthy container means the model is loaded. `WEB_CONCURRENCY` raises the worker count.
- No `app/wsgi.py`: gunicorn calls the existing factory (`app:create_app()`).
- Build context: compose `additional_contexts` names the sibling repository `model`; the Dockerfile copies from it only the package source and the artifact. Nothing of `sereno-model` is copied into this repository or changed there. The image mirrors the host layout (`/srv/sereno-backend`, `/srv/sereno-model`), so the default `MODEL_PATH` resolves unchanged.
- Compose profile `server`: a plain `docker compose up -d --wait` (what `start.ps1` and the test suite use) still starts PostgreSQL and Redis only.
- Tables: nothing new. `create_app` applies the idempotent `app/storage/schema.sql` at startup under an advisory lock, on the host and in the container alike.
- `AUTH_LOCAL_HTTP` is not set in the container. Its guard requires a loopback peer, and behind Docker's port publishing the peer is the bridge gateway, so every route would answer 403. The container uses the secure defaults (`Secure`, `SameSite=None` cookie) and loopback-only access is enforced by publishing the port on `127.0.0.1`. `DEV_TOOLS` is not set either.

## Scope
- `sereno-backend` only: `Dockerfile`, `.dockerignore`, `docker-compose.yml`, `requirements.txt`, `README.md`, `tests/`, this file.
- Not in scope: Azure, TLS, CI, rate limiting, the extension, landing and model repositories, any refactor of `app/`.

## Constraints
English technical artifacts. Conventional commits, no AI attribution. The `postgres-data` volume and the development database are never removed or reset. `start.ps1` keeps working unchanged. The server is reachable at `http://127.0.0.1:5000` only.

## Work units
- [x] C1 Image and compose service (route: single writer): structural tests first, then `Dockerfile`, `.dockerignore`, the `server` service and the gunicorn pin. Commit `0500a78`.
- [x] C2 README: how to bring the three containers up and down, and what differs from the host flow. Commit `3839f58`.
- [x] C3 Functional check with the real model over HTTP against the container, and the full suite.

## Checks
`.venv/Scripts/python.exe -m pytest -q` (400 passed on the base `fd29a32`). `docker compose --profile server up -d --build --wait`, `docker compose ps`, then register, `GET /v1/model` and the same URL twice on `POST /v1/verdict`.

## Progress and evidence
Branch `feat/server-container` from `fd29a32`.

- Structural tests first (`tests/test_server_container.py`, eight tests, and the port count of `tests/test_storage_setup.py`). RED: 9 failed (no `server` service, no `Dockerfile`, no `.dockerignore`, no gunicorn pin, two published ports instead of three). GREEN after the files of C1. The image build has no meaningful runnable RED; it is covered by the functional check below.
- Two fixes found by running the container: `useradd --system` warned about the uid (a plain account is created instead), and gunicorn 27 tried to create its control socket under a home directory the user does not have (`--no-control-socket`).
- `docker compose --profile server up -d --build --wait`: the three containers healthy (`docker compose --profile server ps`); `docker compose exec server id` -> `uid=10001(sereno)`; image `sereno-server:latest`, 1.55 GB (most of it the model libraries). Server log: `Model 2.0.0 loaded in 1.3 s`.
- HTTP check against `http://127.0.0.1:5000` (container, real model): `GET /v1/model` -> `{"version":"2.0.0"}`; register -> 201 with a `Secure; HttpOnly; SameSite=None` cookie; the same new URL twice on `POST /v1/verdict` -> `source: "modelo"` (39 ms) then `source: "cache"` (2 ms), phishing, policy `advertencia`; the two `consulta` rows of that domain are `MISS` (with `prediccion.codigo_version` 2.0.0) and `HIT`; logout -> 200. The access log shows the host requests arriving from `172.18.0.1`, the bridge gateway, which is why `AUTH_LOCAL_HTTP` cannot be used in the container.
- Plain `docker compose up -d --wait` and `docker compose config --services` list `postgres` and `redis` only, so `start.ps1` (unchanged) behaves as before.
- Full suite with the three containers up: `.venv/Scripts/python.exe -m pytest -q` -> 408 passed in 67 s, nothing skipped.

## Open points
- The check left two throwaway accounts in the development database: `container-check-20261114010019` and `container-check-20261114010247` (sessions closed), with their four `consulta` rows. They can be deleted at `/dev/users` in the host flow.
- Not verified here: that Chrome keeps the `Secure` session cookie for `http://127.0.0.1:5000` when the extension signs in against the container. The README already lists real Chrome cookie behaviour as unverified; verdicts do not need a session.
- `start.ps1` does not detect that the container holds port 5000; stop it first (`docker compose --profile server stop server`).
- The image also installs pytest (it is pinned in `requirements.txt`, the only application requirements file).

## Next step
Andrés: manual check with the browser extension against the container, then review the branch and decide the merge.
