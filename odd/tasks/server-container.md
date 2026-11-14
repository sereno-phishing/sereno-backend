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
- [ ] C1 Image and compose service (route: single writer): structural tests first, then `Dockerfile`, `.dockerignore`, the `server` service and the gunicorn pin.
- [ ] C2 README: how to bring the three containers up and down, and what differs from the host flow.
- [ ] C3 Functional check with the real model over HTTP against the container, and the full suite.

## Checks
`.venv/Scripts/python.exe -m pytest -q` (400 passed on the base `fd29a32`). `docker compose --profile server up -d --build --wait`, `docker compose ps`, then register, `GET /v1/model` and the same URL twice on `POST /v1/verdict`.

## Progress and evidence
Branch `feat/server-container` from `fd29a32`.
