"""SQL shared by the adapters that write the `consulta` table: the verdict
journal (one row per served verdict, with its URL hash) and the history and
query-log ports (which carry a domain only)."""

ORIGIN_OF_SOURCE = {"cache": "HIT", "modelo": "MISS"}
SOURCE_OF_ORIGIN = {origin: source for source, origin in ORIGIN_OF_SOURCE.items()}

_URL_BY_HASH = """
    INSERT INTO url_evaluada (hash_url, dominio) VALUES (%s, %s)
    ON CONFLICT (hash_url) DO UPDATE SET dominio = EXCLUDED.dominio
    RETURNING id_url"""
# The hash is unknown: one shared row per domain stands for "a URL of it".
_URL_BY_DOMAIN = """
    INSERT INTO url_evaluada (dominio) VALUES (%s)
    ON CONFLICT (dominio) WHERE hash_url IS NULL DO UPDATE SET dominio = EXCLUDED.dominio
    RETURNING id_url"""
_INSERT = """
    INSERT INTO consulta (id_usuario, id_url, veredicto, politica, puntaje, origen,
                          latencia_ms, fecha)
    VALUES ((SELECT id_usuario FROM usuario WHERE nombre = %s), %s, %s, %s, %s, %s, %s, %s)
    RETURNING id_consulta"""


def insert_consulta(connection, *, username, url_hash, domain, verdict, policy, score, source,
                    latency_ms, at):
    """Adds one served verdict inside the caller's transaction -> id_consulta.
    username None (or unknown) records an anonymous query; url_hash None hangs
    the row from the domain's hashless url_evaluada row."""
    if url_hash is None:
        url_id = connection.execute(_URL_BY_DOMAIN, (domain,)).fetchone()[0]
    else:
        url_id = connection.execute(_URL_BY_HASH, (url_hash, domain)).fetchone()[0]
    return connection.execute(_INSERT, (
        username, url_id, verdict, policy, score, ORIGIN_OF_SOURCE[source], latency_ms,
        at)).fetchone()[0]
