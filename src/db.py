"""Acceso a la base de datos SQLite: conexión y esquema."""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from typing import Iterator

from src.config import DB_PATH

# El DDL es idempotente: se puede ejecutar varias veces sin efecto si ya existe.
DDL = """
PRAGMA foreign_keys = ON;
PRAGMA journal_mode = WAL;

-- ============================================================================
-- licitaciones: una fila por anuncio publicado en PLACSP (identificado por UUID).
-- ============================================================================
CREATE TABLE IF NOT EXISTS licitaciones (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    uuid_placsp TEXT NOT NULL UNIQUE,
    expediente TEXT NOT NULL,
    enlace_placsp TEXT,
    organo_contratacion TEXT,
    organo_nif TEXT,
    objeto TEXT,
    cpv_principal TEXT,
    cpvs_secundarios TEXT,
    importe_sin_iva REAL,
    importe_con_iva REAL,
    moneda TEXT DEFAULT 'EUR',
    provincia TEXT,
    municipio TEXT,
    tipo_contrato TEXT,
    procedimiento TEXT,
    fecha_publicacion TEXT,
    fecha_limite_presentacion TEXT,
    estado TEXT,
    duracion_contrato TEXT,
    lugar_ejecucion TEXT,
    documentos_urls TEXT,
    hash_contenido TEXT,
    pasa_filtros INTEGER,
    motivo_descarte TEXT,
    fecha_ingesta TEXT NOT NULL DEFAULT (datetime('now')),
    fecha_ultima_actualizacion TEXT
);

CREATE INDEX IF NOT EXISTS idx_lic_pasa          ON licitaciones(pasa_filtros);
CREATE INDEX IF NOT EXISTS idx_lic_fecha_limite  ON licitaciones(fecha_limite_presentacion);
CREATE INDEX IF NOT EXISTS idx_lic_provincia     ON licitaciones(provincia);
CREATE INDEX IF NOT EXISTS idx_lic_expediente    ON licitaciones(expediente);

-- ============================================================================
-- pliegos_descargados: PDFs descargados de cada licitación.
-- ============================================================================
CREATE TABLE IF NOT EXISTS pliegos_descargados (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    licitacion_id INTEGER NOT NULL REFERENCES licitaciones(id) ON DELETE CASCADE,
    tipo_documento TEXT,
    url_origen TEXT,
    ruta_local TEXT,
    tamano_bytes INTEGER,
    paginas INTEGER,
    sha256 TEXT,
    texto_extraido_path TEXT,
    extraccion_ok INTEGER DEFAULT 0,
    error TEXT,
    fecha_descarga TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_pliego_sha ON pliegos_descargados(licitacion_id, sha256);
CREATE INDEX        IF NOT EXISTS idx_pliego_lic ON pliegos_descargados(licitacion_id);

-- ============================================================================
-- analisis_ia: una fila por análisis de Claude. Permite reanalizar con prompt
-- distinto manteniendo histórico.
-- ============================================================================
CREATE TABLE IF NOT EXISTS analisis_ia (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    licitacion_id INTEGER NOT NULL REFERENCES licitaciones(id) ON DELETE CASCADE,
    modelo TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    encaje TEXT,
    encaje_score INTEGER,
    motivo TEXT,
    resumen TEXT,
    productos_aplicables TEXT,
    requisitos_criticos TEXT,
    banderas_rojas TEXT,
    respuesta_raw TEXT,
    tokens_entrada INTEGER,
    tokens_salida INTEGER,
    coste_usd REAL,
    fecha_analisis TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_ana_lic     ON analisis_ia(licitacion_id);
CREATE INDEX IF NOT EXISTS idx_ana_encaje  ON analisis_ia(encaje);
CREATE INDEX IF NOT EXISTS idx_ana_fecha   ON analisis_ia(fecha_analisis);

-- ============================================================================
-- log_ejecuciones: traza de cada etapa del pipeline (ingesta, filtros, IA...).
-- ============================================================================
CREATE TABLE IF NOT EXISTS log_ejecuciones (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    fecha_inicio TEXT NOT NULL DEFAULT (datetime('now')),
    fecha_fin TEXT,
    etapa TEXT,
    estado TEXT,
    licitaciones_nuevas INTEGER DEFAULT 0,
    licitaciones_actualizadas INTEGER DEFAULT 0,
    licitaciones_pasan_filtros INTEGER DEFAULT 0,
    pliegos_descargados INTEGER DEFAULT 0,
    analisis_realizados INTEGER DEFAULT 0,
    emails_enviados INTEGER DEFAULT 0,
    coste_ia_usd REAL DEFAULT 0,
    error_mensaje TEXT,
    error_traceback TEXT,
    metadata TEXT
);

CREATE INDEX IF NOT EXISTS idx_log_fecha        ON log_ejecuciones(fecha_inicio);
CREATE INDEX IF NOT EXISTS idx_log_etapa_estado ON log_ejecuciones(etapa, estado);
"""


def conectar(ruta: str | None = None) -> sqlite3.Connection:
    """Abre una conexión a SQLite con configuración estándar del proyecto."""
    ruta = ruta or str(DB_PATH)
    conn = sqlite3.connect(ruta, timeout=30.0, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


@contextmanager
def conexion(ruta: str | None = None) -> Iterator[sqlite3.Connection]:
    """Context manager que abre y cierra una conexión."""
    conn = conectar(ruta)
    try:
        yield conn
    finally:
        conn.close()


def inicializar_esquema(conn: sqlite3.Connection) -> None:
    """Crea todas las tablas e índices si no existen."""
    conn.executescript(DDL)


def listar_tablas(conn: sqlite3.Connection) -> list[str]:
    """Devuelve los nombres de las tablas existentes (excluye internas de SQLite)."""
    filas = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
    ).fetchall()
    return [f["name"] for f in filas]
