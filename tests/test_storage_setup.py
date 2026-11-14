import re
from pathlib import Path

import pytest

from app.config import settings
from app.storage.factory import DRIVERS, build_repositories


ROOT = Path(__file__).resolve().parent.parent
ADD_TABLES = {"usuario", "url_evaluada", "consulta", "prediccion", "politica_dominio",
              "version_modelo"}
AUXILIARY_TABLES = {"sesion", "encuesta_respuesta", "encuesta_participante",
                    "configuracion_cache", "invalidacion_cache", "importacion"}


class Failure(Exception):
    pass


def columns(connection, table):
    return {row[0] for row in connection.execute(
        "SELECT column_name FROM information_schema.columns WHERE table_name = %s", (table,))}


def test_settings_default_to_json_and_the_local_compose_services(monkeypatch):
    for name in ("STORAGE_DRIVER", "DATABASE_URL", "REDIS_URL"):
        monkeypatch.delenv(name, raising=False)
    values = settings()
    assert values["STORAGE_DRIVER"] == "json"
    assert values["DATABASE_URL"] == "postgresql://sereno:sereno@127.0.0.1:5432/sereno"
    assert values["REDIS_URL"] == "redis://127.0.0.1:6379/0"
    monkeypatch.setenv("DATABASE_URL", "postgresql://other@127.0.0.1/x")
    monkeypatch.setenv("REDIS_URL", "redis://127.0.0.1:6379/3")
    values = settings()
    assert values["DATABASE_URL"] == "postgresql://other@127.0.0.1/x"
    assert values["REDIS_URL"] == "redis://127.0.0.1:6379/3"


def test_factory_builds_the_json_adapters_and_rejects_unknown_drivers(tmp_path):
    config = {**settings(), "STORAGE_DRIVER": "json", "DATA_FILE": str(tmp_path / "db.json")}
    repositories = build_repositories(config)
    assert repositories.auth.get_user("missing") is None
    assert repositories.policy.list() == ()
    assert (tmp_path / "cache.json").exists() and (tmp_path / "queries.json").exists()
    assert "json" in DRIVERS
    with pytest.raises(ValueError, match="Unsupported storage driver"):
        build_repositories({**config, "STORAGE_DRIVER": "sqlite"})


def test_compose_services_are_pinned_and_listen_on_loopback_only():
    text = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    images = re.findall(r"^\s+image:\s*(\S+)$", text, flags=re.MULTILINE)
    assert len(images) == 2
    assert all(re.fullmatch(r"(postgres|redis):\d+\.\d+[\w.-]*", image) for image in images)
    ports = re.findall(r'^\s+- "(\S+:\d+)"$', text, flags=re.MULTILINE)
    assert len(ports) == 2 and all(port.startswith("127.0.0.1:") for port in ports)


def test_schema_creates_the_add_tables_and_is_idempotent(database):
    database.apply_schema()
    database.apply_schema()
    with database.transaction(Failure) as connection:
        tables = {row[0] for row in connection.execute(
            "SELECT tablename FROM pg_tables WHERE schemaname = 'public'")}
    assert tables == ADD_TABLES | AUXILIARY_TABLES


def test_no_table_can_hold_a_url(database):
    """RNF-05: an evaluated URL is represented by its hash and its domain only."""
    with database.transaction(Failure) as connection:
        assert columns(connection, "url_evaluada") == {"id_url", "hash_url", "dominio"}
        names = {row[0] for row in connection.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_schema = 'public'")}
    assert not any("url" in name and name not in ("id_url", "hash_url") for name in names)


def test_a_transaction_commits_on_success_and_rolls_back_on_failure(database):
    insert = ("INSERT INTO politica_dominio (dominio, politica, fecha_actualizacion) "
              "VALUES (%s, 'bloqueo', '2026-11-13')")
    with database.transaction(Failure) as connection:
        connection.execute(insert, ("kept.pe",))
    with pytest.raises(Failure):
        with database.transaction(Failure) as connection:
            connection.execute(insert, ("dropped.pe",))
            connection.execute(insert, ("kept.pe",))  # unique violation
    with database.transaction(Failure) as connection:
        assert connection.execute("SELECT dominio FROM politica_dominio").fetchall() == [
            ("kept.pe",)]


def test_an_unreachable_database_raises_the_callers_storage_error():
    pytest.importorskip("psycopg")
    from app.storage.postgres import Database

    database = Database("postgresql://sereno:sereno@127.0.0.1:1/sereno_test", timeout=0.5)
    try:
        with pytest.raises(Failure, match="Storage unavailable"):
            with database.transaction(Failure):
                pass
    finally:
        database.close()
