# Azure deployment

## Objective
Make the three-container stack (PostgreSQL, Redis, evaluation server) deployable on one small Azure VM (Ubuntu, x64, 1 GiB RAM plus swap) behind HTTPS, as the architecture describes: REST over HTTPS/TLS between the extension and the server, the server behind a firewall, Flask, Redis and PostgreSQL each in a Docker container on one VM.

## Problem and rationale
`docker-compose.yml` is a development setup: development default credentials, ports published on loopback for the host flow, the server behind the `server` profile and plain HTTP on `127.0.0.1:5000`. A public deployment needs TLS, a single public entry point, required secrets and no development switches. The public hostname is an Azure DNS label (`<label>.<region>.cloudapp.azure.com`) not known yet, so it is a variable.

## Decisions
- **Override file, not a standalone file.** `docker-compose.prod.yml` is applied on top of `docker-compose.yml` (`-f docker-compose.yml -f docker-compose.prod.yml`). The images, build context, healthchecks and hardening of the base file are reused instead of copied, so the two cannot drift; the development behaviour of the base file is unchanged. Compose `!reset` removes what production must not inherit (published ports, the `server` profile).
- **Caddy as the only public service.** `caddy:2.10.2-alpine` publishes 80/tcp, 443/tcp and 443/udp (HTTP/3) and reverse-proxies `{$SERENO_DOMAIN}` to `server:5000`; it obtains and renews the Let's Encrypt certificate by itself and redirects HTTP to HTTPS. Certificates live in the named volumes `caddy-data` and `caddy-config`. With `SERENO_DOMAIN=localhost` Caddy issues a certificate from its local CA instead (local check only).
- **No published port for postgres, redis, server** in production. Administration goes through `docker compose exec` (for example `psql`).
- **Required variables.** `POSTGRES_PASSWORD` and `SERENO_DOMAIN` use `${VAR:?message}`, so `config`/`up` fail without them; the development default `sereno` password is never used. The password is part of `DATABASE_URL`, so it must be URL-safe (hex).
- **Development switches forced off.** `AUTH_LOCAL_HTTP=0` and `DEV_TOOLS=0` are set explicitly in the server environment (any value other than `1` is off in `app/config.py`). Without `AUTH_LOCAL_HTTP` the session cookie is `Secure; HttpOnly; SameSite=None`, which the extension needs over HTTPS.
- **No ProxyFix.** The only reads of `request.remote_addr`/`request.host_url` are in `direct_loopback()` (`app/http.py`), used only by `guard_local_http` (inactive without `AUTH_LOCAL_HTTP`) and the `/dev` routes (not registered without `DEV_TOOLS`). There is no `url_for`, redirect or `request.scheme` use, and the cookie's `Secure` flag does not depend on the scheme. Trusting forwarded headers would add nothing and could weaken `direct_loopback()`, so `app/` is unchanged. Caddy's `X-Forwarded-*` headers would make `/dev` answer 404 even if `DEV_TOOLS` were enabled by mistake.
- **Memory (1 GiB VM).** PostgreSQL: `shared_buffers=32MB`, `max_connections=25` (the server pool opens at most 8 per worker, one worker). Redis: `maxmemory 64mb` with `volatile-ttl`; every cache key has a TTL (entries and `dominio:*` indexes, `app/cache/redis_repository.py`) and the cache is disposable, so eviction only drops entries early, shortest remaining TTL first. No container memory limits: the model load peak was not measured, and a hard limit could kill the server on load; swap on the VM covers peaks.
- **`restart: unless-stopped`** on the four services. `CLASSIFIER=model`, model 3.0.0 (from the Dockerfile and the default `MODEL_PATH`) unchanged.
- **Template at `deploy/production.env.example`.** The planned name `.env.production.example` is blocked for writing by the local agent permission settings (`.env*`); a name outside the `.env*` patterns also needs no `.gitignore` exception, and real `.env` files stay ignored by the existing `.env`/`.env.*` rules.
- **`COMPOSE_FILE` in the VM's `.env`.** `deploy/production.env.example` sets `COMPOSE_FILE=docker-compose.yml:docker-compose.prod.yml`, so every later `docker compose` command on the VM (ps, logs, exec, down) uses both files without repeating `-f`.

## Scope
- `sereno-backend` only: `docker-compose.prod.yml`, `Caddyfile`, `deploy/production.env.example`, `README.md`, this file.
- Not in scope: creating the Azure resources, DNS, CI/CD, image registry, rate limiting, an administrator bootstrap path, the extension (its host permission and server address), the other repositories.

## Constraints
English technical artifacts. Conventional commits, no AI attribution. `docker-compose.yml` and its development behaviour unchanged. No remote operations.

## Work units
- [x] D1 Production compose override, Caddyfile, `deploy/production.env.example` (route: delegated writer, this worker).
- [x] D2 README deployment section.
- [x] D3 Checks: rendered config with dummy values, failure without `POSTGRES_PASSWORD` and without `SERENO_DOMAIN`. Full test suite and live stack: pending (Docker daemon not running).

## Checks
- `docker compose -f docker-compose.yml -f docker-compose.prod.yml config` with dummy `POSTGRES_PASSWORD` and `SERENO_DOMAIN`: renders, only `caddy` has published ports, `server` has no profile.
- The same without `POSTGRES_PASSWORD` (and without `SERENO_DOMAIN`): fails with the message.
- `docker compose config --services` (base only): still `postgres`, `redis`.
- `.venv/Scripts/python.exe -m pytest -q`: no regression.
- Optional: the stack up locally with `SERENO_DOMAIN=localhost`, `curl -k https://localhost/v1/model`.

Test-first exception: the deliverables are configuration and documentation; the allowed edit surface has no test files and the behaviour (TLS, ports) is only observable through `docker compose config` and a running stack, which are the checks above.

## Progress and evidence
Branch `feat/azure-deployment` from `60bb7f0`.

- `POSTGRES_PASSWORD=dummyhex SERENO_DOMAIN=example.cloudapp.azure.com docker compose -f docker-compose.yml -f docker-compose.prod.yml config` -> exit 0. Published ports only on `caddy` (80, 443, 443/udp); `postgres`, `redis`, `server` have none; `server` has no profile, `AUTH_LOCAL_HTTP: "0"`, `DEV_TOOLS: "0"`, `DATABASE_URL: postgresql://sereno:dummyhex@postgres:5432/sereno`, `CLASSIFIER: model`; `restart: unless-stopped` on the four; volumes `caddy-data`, `caddy-config`, `postgres-data`.
- Without `POSTGRES_PASSWORD` (and with it empty) -> exit 1, `required variable POSTGRES_PASSWORD is missing a value` for `postgres` and `server`. Without `SERENO_DOMAIN` -> exit 1, `required variable SERENO_DOMAIN is missing a value`.
- The template with a filled password, as an env file -> `config --services` lists postgres, redis, server, caddy (COMPOSE_FILE honoured); the unfilled template -> exit 1.
- Base file unchanged: `docker compose config --services` -> postgres, redis; `--profile server` still publishes 5432, 6379, 5000 on `127.0.0.1`.
- `.venv/Scripts/python.exe -m pytest -q` -> stops at once, `PostgreSQL and Redis unreachable` (Docker Desktop not running). With `--noconftest`, `tests/test_server_container.py tests/test_storage_setup.py` -> 11 passed, 1 failed, 3 errors, identical on the base; all four need the services. No Python, `Dockerfile` or `docker-compose.yml` change, so no regression is expected, but the full suite was not run.
- Live check (`SERENO_DOMAIN=localhost`, `curl -k https://localhost/v1/model`) not run: Docker daemon not running. The Caddy image tag and the `Caddyfile` were therefore not exercised.

## Open points
- Administrator accounts: there is no production path in the product (no endpoint, no CLI). Public registration always creates a `user`; the only way is the documented SQL `UPDATE usuario SET rol = 'admin' ...` run with `docker compose exec postgres psql` on the VM after registering the account.
- Building the image on a 1 GiB VM installs the model libraries with pip; it needs the swap and may be slow. Building elsewhere and pushing to a registry is not set up.
- Caddy keeps the image's default capabilities (no `cap_drop`); the image sets a file capability on the binary, and dropping all capabilities without adding `NET_BIND_SERVICE` back makes it fail to start. Not tightened because it could not be run here.
- The extension still targets `http://127.0.0.1:5000`; pointing it at the HTTPS host is a change in `sereno-extension`.

## Next step
Andrés: create the VM, the DNS label and the NSG rules (80, 443 tcp/udp, SSH restricted), deploy with the README steps, then review the branch and decide the merge.
