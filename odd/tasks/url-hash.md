# URL hash and data minimisation (thesis inconsistency I-51)

## Objective
Make the backend fulfil the data-minimisation promises of the approved architecture (ADD, RNF-05, RF-12) and the published privacy policy: the Redis cache keyed by the hash of the normalised URL, PostgreSQL holding only the hash and the domain of each evaluated URL, and the full URL processed in memory only, never persisted nor logged.

## Problem and rationale
I-51 reported that the backend stored the domain in plain text and computed no URL hash. Mapping the code showed that report is out of date (see Findings): the hash, the normalisation and the hash-only storage exist since `7a63643`. The real gap is narrower: one log line could carry the full URL.

## Findings (verified on `6e12ee8`, branch `feat/azure-deployment`)
- Normalisation: `app/verdict/url.py` uses `sereno_model.data.normalize.parse_url` (lower-case scheme and host, default port dropped, fragment dropped, unreserved percent-encodings decoded; http/https, `www.`, trailing slash and query order kept distinct).
- Cache key: `cache_key()` = hex SHA-256 of the normalised URL. Redis holds `<hash> -> {domain, verdict, score, cachedAt}` and the index set `dominio:<domain>` (used by invalidation by domain). No URL.
- PostgreSQL: `url_evaluada (id_url, hash_url CHAR(64) UNIQUE, dominio)`; `consulta` references `id_url`. No URL column anywhere (`tests/test_storage_setup.py` already checks this). History and metrics read `dominio`.
- HTTP: the URL travels in the JSON body of `POST /v1/verdict`, so the gunicorn access log and Caddy (no `log` directive) only see the path.
- Logs: the cache-outage warning carries the domain and the hash only. **Gap:** `VerdictService._classify` logged `Classifier failed: %s` with `str(error)`. The model's `score_url` re-parses the URL with `parse_url`, whose `InvalidUrlError` message embeds `repr(url)`, and any other classifier exception may quote its input, so the full URL could reach the server log.

## Decisions
- No schema change and no migration: the hash column exists, is `NOT NULL` and `UNIQUE` since the first PostgreSQL schema, so every existing row already has its hash.
- Redis entries keep the domain beside the verdict (allowed data; it is what invalidation by domain and the `dominio:<domain>` index need) and the score (a model output, not user data, returned to the extension on a hit).
- Classifier failure log: never the exception text of an arbitrary error. The port gains `ClassifierUnavailable`, for errors whose message is written by the adapter and never quotes the URL; `ModelUnavailable` (missing artifact or libraries) subclasses it, so that actionable message is still logged. Any other error is logged by type only. Every line carries the domain and the URL hash, like the cache-outage warning.

## Scope
- `app/verdict/classifier.py`, `app/verdict/model_classifier.py`, `app/verdict/service.py`, `tests/`, `README.md`, this file.
- Not in scope: the extension, the model repository, `tools/response_time_experiment.py` (a client-side experiment tool whose `requests.csv` lists the test URLs it sent; it is not the server).

## Constraints
English technical artifacts. Conventional commits, no AI attribution. No push, no merge. Development database and volumes untouched.

## Work units
- [x] U1 Requirement tests (route: inline, single writer): normalisation cases of the design, hash equality/inequality, no URL in the classifier-failure log. RED, then the service/port fix, GREEN. Commit `e88e93b`.
- [x] U2 README: what is stored and what is logged. Commit `2ddd44d`.
- [x] U3 Full suite and readback.

## Checks
`docker compose up -d --wait`; `.venv/Scripts/python.exe -m pytest -q` (448 passed on the base `6e12ee8`); `docker compose down` afterwards (volumes kept).

## Progress and evidence
Branch `feat/url-hash` from `6e12ee8` (`feat/azure-deployment`).

- RED (`tests/test_verdict.py`): 3 failed, 65 passed. The leak was observed in the captured log: `Classifier failed: cannot score 'https://www.Shop.pe/reset?token=s3cr3t#frag'`; the other two failures were the missing `ClassifierUnavailable`. The 18 new normalisation and hash cases passed at once: they pin behaviour that already existed.
- GREEN: `tests/test_verdict.py` and `tests/test_model_classifier.py` 91 passed.
- Full suite: `.venv/Scripts/python.exe -m pytest -q` -> 469 passed (448 on the base plus 21 new), nothing skipped.

## Open points
- I-51 in the thesis context should be closed with the findings above: the hash, the normalisation and the hash-only storage predate this branch; the only gap was the log line fixed here.
- Redis entries also hold the score (model output) besides the hash, verdict, date and domain; the ADD wording "hash, verdict and date" may need the domain and the score added.

## Next step
Andrés: review the branch and decide the merge order with `feat/azure-deployment`.
