-- Sereno relational schema (PostgreSQL), applied at every start with
-- STORAGE_DRIVER=postgres. Every statement is idempotent.
--
-- RNF-05: no table stores a URL. An evaluated URL is represented by the
-- SHA-256 of its normalized form and by its domain.
--
-- The first six tables are the ones the architecture document (ADD) defines.
-- The rest are auxiliary tables for data the ADD gives no table for.

CREATE TABLE IF NOT EXISTS usuario (
    id_usuario      INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    nombre          TEXT NOT NULL UNIQUE,
    hash_contrasena TEXT NOT NULL,
    rol             TEXT NOT NULL CHECK (rol IN ('user', 'admin')),
    fecha_registro  TIMESTAMPTZ NOT NULL
);

-- hash_url is NULL only for records imported from the JSON files, which kept
-- the domain but never the hash; such a row stands for "some URL of the domain".
CREATE TABLE IF NOT EXISTS url_evaluada (
    id_url   INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    hash_url CHAR(64) UNIQUE CHECK (hash_url ~ '^[0-9a-f]{64}$'),
    dominio  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS url_evaluada_dominio ON url_evaluada (dominio);
CREATE UNIQUE INDEX IF NOT EXISTS url_evaluada_dominio_sin_hash
    ON url_evaluada (dominio) WHERE hash_url IS NULL;

CREATE TABLE IF NOT EXISTS version_modelo (
    codigo           VARCHAR(20) PRIMARY KEY,
    fecha            TIMESTAMPTZ NOT NULL,
    reporte_metricas JSONB
);

-- One row per served verdict: it backs both the per-user history and the
-- dashboard metrics. veredicto, politica and puntaje are the values that were
-- answered; puntaje is NULL only for imported query-log records, which never
-- kept it. Clearing a history sets id_usuario to NULL.
CREATE TABLE IF NOT EXISTS consulta (
    id_consulta BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    id_usuario  INTEGER REFERENCES usuario (id_usuario) ON DELETE SET NULL,
    id_url      INTEGER NOT NULL REFERENCES url_evaluada (id_url),
    veredicto   TEXT NOT NULL CHECK (veredicto IN ('phishing', 'legitima')),
    politica    TEXT NOT NULL CHECK (politica IN ('advertencia', 'bloqueo')),
    puntaje     DOUBLE PRECISION CHECK (puntaje BETWEEN 0 AND 1),
    origen      TEXT NOT NULL CHECK (origen IN ('HIT', 'MISS')),
    latencia_ms INTEGER NOT NULL CHECK (latencia_ms >= 0),
    fecha       TIMESTAMPTZ NOT NULL
);
CREATE INDEX IF NOT EXISTS consulta_usuario
    ON consulta (id_usuario, id_consulta DESC) WHERE id_usuario IS NOT NULL;

-- What the model answered on a MISS; a HIT has no row here.
CREATE TABLE IF NOT EXISTS prediccion (
    id_prediccion  BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    id_consulta    BIGINT NOT NULL UNIQUE REFERENCES consulta (id_consulta) ON DELETE CASCADE,
    veredicto      TEXT NOT NULL CHECK (veredicto IN ('phishing', 'legitima')),
    puntaje        DOUBLE PRECISION NOT NULL CHECK (puntaje BETWEEN 0 AND 1),
    codigo_version VARCHAR(20) NOT NULL REFERENCES version_modelo (codigo),
    fecha          TIMESTAMPTZ NOT NULL
);

-- Related to url_evaluada by the domain value, without a foreign key.
CREATE TABLE IF NOT EXISTS politica_dominio (
    id_politica         INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    dominio             TEXT NOT NULL UNIQUE,
    politica            TEXT NOT NULL CHECK (politica IN ('advertencia', 'bloqueo')),
    fecha_actualizacion DATE NOT NULL
);

-- Auxiliary tables.

CREATE TABLE IF NOT EXISTS sesion (
    token        TEXT PRIMARY KEY,
    id_usuario   INTEGER NOT NULL REFERENCES usuario (id_usuario) ON DELETE CASCADE,
    fecha_inicio TIMESTAMPTZ NOT NULL
);

-- SUS survey: responses carry no reference to who sent them; participation is
-- kept apart, without a date or a sequence that could pair it with a response.
CREATE TABLE IF NOT EXISTS encuesta_respuesta (
    id_respuesta INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    respuestas   SMALLINT[] NOT NULL CHECK (cardinality(respuestas) = 10),
    fecha_envio  TIMESTAMPTZ NOT NULL
);
CREATE TABLE IF NOT EXISTS encuesta_participante (
    id_usuario INTEGER PRIMARY KEY REFERENCES usuario (id_usuario) ON DELETE CASCADE
);

-- Verdict cache administration (the entries themselves live in Redis).
CREATE TABLE IF NOT EXISTS configuracion_cache (
    id                 SMALLINT PRIMARY KEY CHECK (id = 1),
    ttl_phishing_dias  INTEGER NOT NULL CHECK (ttl_phishing_dias > 0),
    ttl_legitima_horas INTEGER NOT NULL CHECK (ttl_legitima_horas > 0)
);
CREATE TABLE IF NOT EXISTS invalidacion_cache (
    id_invalidacion INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    dominio         TEXT NOT NULL,
    fecha           DATE NOT NULL,
    veredicto       TEXT CHECK (veredicto IN ('phishing', 'legitima'))
);

-- Sections of the local JSON data already loaded by app.tools.import_json.
CREATE TABLE IF NOT EXISTS importacion (
    seccion TEXT PRIMARY KEY,
    fecha   TIMESTAMPTZ NOT NULL DEFAULT now()
);
