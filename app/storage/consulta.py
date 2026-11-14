"""The `consulta` table shared by its adapters: the verdict journal writes one
row per served verdict, the history and the query log read them."""

ORIGIN_OF_SOURCE = {"cache": "HIT", "modelo": "MISS"}
SOURCE_OF_ORIGIN = {origin: source for source, origin in ORIGIN_OF_SOURCE.items()}

_URL = """
    INSERT INTO url_evaluada (hash_url, dominio) VALUES (%s, %s)
    ON CONFLICT (hash_url) DO UPDATE SET dominio = EXCLUDED.dominio
    RETURNING id_url"""
_INSERT = """
    INSERT INTO consulta (id_usuario, id_url, veredicto, politica, puntaje, origen,
                          latencia_ms, fecha)
    VALUES ((SELECT id_usuario FROM usuario WHERE nombre = %s), %s, %s, %s, %s, %s, %s, %s)
    RETURNING id_consulta"""


def insert_consulta(connection, *, username, url_hash, domain, verdict, policy, score, source,
                    latency_ms, at):
    """Adds one served verdict inside the caller's transaction -> id_consulta.
    username None (or unknown) records an anonymous query."""
    url_id = connection.execute(_URL, (url_hash, domain)).fetchone()[0]
    return connection.execute(_INSERT, (
        username, url_id, verdict, policy, score, ORIGIN_OF_SOURCE[source], latency_ms,
        at)).fetchone()[0]
