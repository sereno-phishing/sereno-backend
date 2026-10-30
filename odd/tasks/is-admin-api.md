# Language-neutral administrator flag in the auth API

## Objective
Stop exposing Spanish role names (`usuario`, `administrador`) through the API. Sessions and users expose `isAdmin: boolean`; the extension translates any label itself.

## Problem and rationale
The extension shows the raw backend `role` (e.g. "Role: administrador" in English UI) and compares it with a Spanish string. Presentation language must live in the front end; the API should carry a boolean.

## Authorized scope
- sereno-backend: stored role values become neutral `user` / `admin`; API responses replace `role` with `isAdmin`; admin authorization in `http.py` uses the flag; tests and README updated.
- sereno-extension: consume `isAdmin`; translate the role label from it; mock seed and provider contract updated; tests and README updated.
- No survey translation, no ML, no remote operations, no academic documents. Runtime JSON is migrated manually by the user, never by code.

## Constraints
English technical artifacts. Strict TDD (pytest / node --test), RED before GREEN. Unknown stored roles still fail closed. Persisted legacy values (`usuario`, `administrador`) are rejected, not silently converted.

## Work units
- [x] T1 Backend: `isAdmin` in user/session payloads, neutral stored roles, tests, README.
- [x] T2 Extension: consume `isAdmin`, language-neutral role label, seed/ports/tests/README.

## Next step
T1 and T2 implemented (backend 229 passed, extension 462 passed); awaiting parent commit.
