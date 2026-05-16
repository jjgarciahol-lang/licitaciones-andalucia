"""Acceso a la base de datos SQLite del subproyecto 'mapa': conexión y esquema.

Separada de src/db.py (licitaciones) a propósito: dominios distintos,
pipelines distintos, riesgo de mezclar cero.
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from typing import Iterator

from src.mapa.config_mapa import CLIENTES_DB_PATH

# DDL idempotente — se puede ejecutar varias veces sin efecto si ya existe.
DDL = """
PRAGMA foreign_keys = ON;
PRAGMA journal_mode = WAL;

-- ============================================================================
-- clientes: una fila por cliente potencial (colegio, ayuntamiento, guardería,
-- hotel). El id es un hash estable basado en (fuente, codigo_origen) para que
-- reejecutar la ingesta no genere duplicados.
-- ============================================================================
CREATE TABLE IF NOT EXISTS clientes (
    id TEXT PRIMARY KEY,
    tipo TEXT NOT NULL,
    nombre TEXT NOT NULL,
    direccion TEXT,
    municipio TEXT,
    provincia TEXT NOT NULL,
    cp TEXT,
    telefono TEXT,
    email TEXT,
    web TEXT,
    lat REAL,
    lon REAL,
    etapas TEXT,
    fuente TEXT NOT NULL,
    codigo_origen TEXT,
    confianza TEXT NOT NULL DEFAULT 'alta',
    fecha_actualizacion TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_cli_tipo       ON clientes(tipo);
CREATE INDEX IF NOT EXISTS idx_cli_provincia  ON clientes(provincia);
CREATE INDEX IF NOT EXISTS idx_cli_municipio  ON clientes(municipio);
CREATE INDEX IF NOT EXISTS idx_cli_fuente     ON clientes(fuente);
CREATE INDEX IF NOT EXISTS idx_cli_sin_coord  ON clientes(lat) WHERE lat IS NULL;
"""


def conectar(ruta: str | None = None) -> sqlite3.Connection:
    """Abre una conexión a la DB del mapa con configuración estándar."""
    ruta = ruta or str(CLIENTES_DB_PATH)
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
    """Crea las tablas e índices del subproyecto mapa si no existen."""
    conn.executescript(DDL)


def listar_tablas(conn: sqlite3.Connection) -> list[str]:
    """Devuelve los nombres de las tablas existentes (excluye internas)."""
    filas = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
    ).fetchall()
    return [f["name"] for f in filas]
