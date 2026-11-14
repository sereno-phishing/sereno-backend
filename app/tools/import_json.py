"""One-off import of the local JSON data into PostgreSQL.

    .venv/Scripts/python.exe -m app.tools.import_json

It reads the files the JSON driver wrote (DATA_FILE and the files beside it,
resolved exactly as the backend does) and loads them into the database named
by DATABASE_URL. The JSON files are only read: never modified, created or
deleted. Each section is one transaction and is recorded in the `importacion`
table, so running the command again imports nothing twice. Rows PostgreSQL
already has are kept. The report says what was imported and what was skipped."""
import json
import sys
from datetime import datetime, timedelta, timezone

from ..auth.domain import Session, User
from ..auth.json_repository import JsonAuthRepository
from ..cache.json_repository import JsonCacheRepository
from ..config import (cache_data_path, data_path, history_data_path, policy_data_path,
                      query_log_data_path, settings, survey_data_path)
from ..history.domain import STAMP_FORMAT, parse_stamp
from ..history.json_repository import JsonHistoryRepository
from ..metrics.json_repository import JsonQueryLogRepository
from ..policy.json_repository import JsonPolicyRepository
from ..storage.consulta import insert_consulta
from ..storage.postgres import Database, DatabaseUnavailable
from ..survey.json_repository import JsonSurveyRepository


AUTH_STAMP = "%Y-%m-%dT%H:%M"
SURVEY_STAMP = "%Y-%m-%dT%H:%M:%SZ"
HASH_NOTE = ("  Imported queries have no URL hash (the JSON files never kept it): each hangs "
             "from the url_evaluada row of its domain with hash_url NULL, and has no "
             "prediccion row because the model version that answered is unknown.")


class ImportFailed(Exception):
    pass


def _utc(stamp, layout):
    return datetime.strptime(stamp, layout).replace(tzinfo=timezone.utc)


def _plural(count, noun, plural=None):
    return f"{count} {noun if count == 1 else plural or noun + 's'}"


# Readers: each returns the validated content of its files, or None when the
# file does not exist. They never write (the JSON adapters only create a file
# that is missing, and these are checked to exist first).

def read_accounts(path):
    if not path.is_file():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    JsonAuthRepository._validate(data)
    return data


def read_policies(path):
    return JsonPolicyRepository(path).list() if path.is_file() else None


def read_survey(path):
    if not path.is_file():
        return None
    submissions = JsonSurveyRepository(path).list_submissions()
    participants = json.loads(path.read_text(encoding="utf-8")).get("participants", [])
    return submissions, participants


def read_cache(path):
    return JsonCacheRepository(path).read() if path.is_file() else None


def read_queries(log_path, history_path):
    if not log_path.is_file() and not history_path.is_file():
        return None
    events = JsonQueryLogRepository(log_path).list() if log_path.is_file() else ()
    history = JsonHistoryRepository(history_path).all() if history_path.is_file() else {}
    return events, history


# Writers: each runs inside the section's transaction and returns report lines.

def same_accounts(connection, accounts):
    """Names whose PostgreSQL account is the JSON one (same password hash). A
    name that exists with another hash is a different account: nothing of the
    JSON user may be attached to it."""
    users = (accounts or {}).get("users", {})
    stored = dict(connection.execute(
        "SELECT nombre, hash_contrasena FROM usuario WHERE nombre = ANY(%s)",
        (list(users),)).fetchall())
    return {name for name, record in users.items()
            if stored.get(name) == record["password_hash"]}


def write_accounts(connection, data, accounts):
    imported, taken = [], []
    for record in data["users"].values():
        user = User(**record)
        inserted = connection.execute(
            "INSERT INTO usuario (nombre, hash_contrasena, rol, fecha_registro) "
            "VALUES (%s, %s, %s, %s) ON CONFLICT (nombre) DO NOTHING",
            (user.username, user.password_hash, user.role,
             _utc(user.createdAt, AUTH_STAMP))).rowcount
        (imported if inserted else taken).append(user.username)
    sessions = skipped_sessions = 0
    for record in data["sessions"].values():
        session = Session(**record)
        stored = session.username in imported and connection.execute(
            "INSERT INTO sesion (token, id_usuario, fecha_inicio) "
            "SELECT %s, id_usuario, %s FROM usuario WHERE nombre = %s "
            "ON CONFLICT (token) DO NOTHING",
            (session.token, _utc(session.loginAt, AUTH_STAMP), session.username)).rowcount
        sessions += bool(stored)
        skipped_sessions += not stored
    lines = [f"Accounts: {len(imported)} imported, {len(taken)} skipped; "
             f"sessions: {sessions} imported, {skipped_sessions} skipped."]
    lines += [f"  Account '{name}' skipped with its sessions: the name already exists in "
              "PostgreSQL." for name in taken]
    return lines


def write_policies(connection, rows, accounts):
    kept = []
    for row in rows:
        inserted = connection.execute(
            "INSERT INTO politica_dominio (dominio, politica, fecha_actualizacion) "
            "VALUES (%s, %s, %s) ON CONFLICT (dominio) DO NOTHING",
            (row.domain, row.policy, row.updatedAt)).rowcount
        if not inserted:
            kept.append(row.domain)
    lines = [f"Domain policies: {len(rows) - len(kept)} imported, {len(kept)} skipped."]
    lines += [f"  Policy of '{domain}' skipped: PostgreSQL already has one." for domain in kept]
    return lines


def write_survey(connection, source, accounts):
    submissions, participants = source
    for submission in submissions:
        connection.execute(
            "INSERT INTO encuesta_respuesta (respuestas, fecha_envio) VALUES (%s, %s)",
            (list(submission.answers), _utc(submission.submittedAt, SURVEY_STAMP)))
    known = same_accounts(connection, accounts)
    marked, skipped = 0, []
    for name in participants:
        done = name in known and connection.execute(
            "INSERT INTO encuesta_participante (id_usuario) "
            "SELECT id_usuario FROM usuario WHERE nombre = %s "
            "ON CONFLICT (id_usuario) DO NOTHING", (name,)).rowcount
        if done:
            marked += 1
        else:
            skipped.append(name)
    lines = [f"Survey: {_plural(len(submissions), 'response')} imported; "
             f"participants: {marked} marked, {len(skipped)} skipped."]
    lines += [f"  Participant '{name}' skipped: no imported account with that name, or it "
              "already took part in PostgreSQL." for name in skipped]
    return lines


def write_cache(connection, state, accounts):
    configured = connection.execute(
        "INSERT INTO configuracion_cache (id, ttl_phishing_dias, ttl_legitima_horas) "
        "VALUES (1, %s, %s) ON CONFLICT (id) DO NOTHING",
        (state.ttlPhishingDays, state.ttlLegitHours)).rowcount
    # Stored newest first; inserted oldest first so the newest gets the highest id.
    for item in reversed(state.invalidations):
        connection.execute(
            "INSERT INTO invalidacion_cache (dominio, fecha, veredicto) VALUES (%s, %s, %s)",
            (item.domain, item.date, item.verdict))
    ttl = ("TTL settings imported" if configured
           else "TTL settings skipped (PostgreSQL already has them)")
    return [f"Cache: {ttl}; {_plural(len(state.invalidations), 'invalidation')} imported; "
            f"{_plural(len(state.entries), 'cache entry', 'cache entries')} "
            "not imported (Redis starts empty and each URL is evaluated again on demand)."]


def _pairs_with(record, event):
    """Whether a history record and a query-log event describe the same served
    verdict. The history stamp is the local minute; it was taken just after
    the event's instant, so it may fall in the next minute."""
    if (record.domain, record.verdict, record.policy, record.source) != (
            event.domain, event.verdict, event.policy, event.source):
        return False
    if record.latencyMs is not None and record.latencyMs != event.latencyMs:
        return False
    minute = event.at.replace(tzinfo=None, second=0, microsecond=0)
    return parse_stamp(record.ts) - minute in (timedelta(0), timedelta(minutes=1))


def write_queries(connection, source, accounts):
    """The query log becomes anonymous `consulta` rows. A history record is
    the same served verdict seen by its user: it is paired with its query-log
    event (which then gets the user and the score) so nothing counts twice in
    the metrics; a record with no event left becomes a row of its own."""
    events, history = source
    known = same_accounts(connection, accounts)
    owners = [None] * len(events)  # (username, score) of the history record paired with each
    extra, lines = [], []
    for username, records in history.items():
        if username not in known:
            if records:
                lines.append(f"  History of '{username}' skipped ({_plural(len(records), 'record')}): "
                             "no imported account with that name.")
            continue
        paired = alone = unusable = 0
        for record in reversed(records):  # stored newest first
            match = next((index for index, event in enumerate(events)
                          if owners[index] is None and _pairs_with(record, event)), None)
            if match is not None:
                owners[match] = (username, record.score)
                paired += 1
            elif record.latencyMs is None:
                unusable += 1  # a query needs its latency
            else:
                extra.append((parse_stamp(record.ts).astimezone(), username, record))
                alone += 1
        lines.append(f"  History of '{username}': {paired} paired with their query-log event, "
                     f"{alone} without a query-log event added as queries of their own, "
                     f"{unusable} skipped (no latency).")
    rows = [(event.at, owner[0] if owner else None, event, owner[1] if owner else None,
             event.latencyMs) for event, owner in zip(events, owners)]
    rows += [(at, username, record, record.score, record.latencyMs)
             for at, username, record in extra]
    # Inserted in time order: the history lists a user's rows newest id first.
    for at, username, item, score, latency in sorted(rows, key=lambda row: row[0]):
        insert_consulta(connection, username=username, url_hash=None, domain=item.domain,
                        verdict=item.verdict, policy=item.policy, score=score,
                        source=item.source, latency_ms=latency, at=at)
    return [f"Queries: {_plural(len(events), 'query-log event')} imported.", *lines, HASH_NOTE]


def sections(config):
    """-> [(section key, file names, read() -> source | None, write)]."""
    auth_file = config["DATA_FILE"]
    accounts = data_path(auth_file)
    policies = policy_data_path(auth_file, config.get("POLICY_DATA_FILE"))
    survey = survey_data_path(auth_file, config.get("SURVEY_DATA_FILE"))
    cache = cache_data_path(auth_file, config.get("CACHE_DATA_FILE"))
    history = history_data_path(auth_file, config.get("HISTORY_DATA_FILE"))
    queries = query_log_data_path(auth_file, config.get("QUERY_LOG_DATA_FILE"))
    return [
        ("cuentas", accounts.name, lambda: read_accounts(accounts), write_accounts),
        ("politicas", policies.name, lambda: read_policies(policies), write_policies),
        ("encuesta", survey.name, lambda: read_survey(survey), write_survey),
        ("cache", cache.name, lambda: read_cache(cache), write_cache),
        ("consultas", f"{queries.name} and {history.name}",
         lambda: read_queries(queries, history), write_queries),
    ]


def import_json(config, database):
    """Loads every section that is present and not yet imported -> report lines."""
    plan = sections(config)
    try:
        accounts = plan[0][2]()
    except Exception:
        accounts = None  # reported by the accounts section below
    lines = []
    for key, files, read, write in plan:
        try:
            source = read()
        except Exception as error:
            lines.append(f"{files} could not be read ({type(error).__name__}); "
                         "nothing imported from it.")
            continue
        if source is None:
            lines.append(f"{files} not found; nothing to import.")
            continue
        with database.transaction(ImportFailed) as connection:
            done = connection.execute(
                "SELECT fecha FROM importacion WHERE seccion = %s", (key,)).fetchone()
            if done:
                lines.append(f"{files} already imported on {done[0]:%Y-%m-%d %H:%M}; skipped.")
                continue
            lines += write(connection, source, accounts)
            connection.execute("INSERT INTO importacion (seccion) VALUES (%s)", (key,))
    return lines


def main():
    config = settings()
    database = Database(config["DATABASE_URL"])
    try:
        database.apply_schema()
        for line in import_json(config, database):
            print(line)
    except (DatabaseUnavailable, ImportFailed) as error:
        print("Import failed: PostgreSQL is unreachable or rejected the data "
              f"({error.__cause__ or error}). Nothing of the failed section was kept.",
              file=sys.stderr)
        return 1
    finally:
        database.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
