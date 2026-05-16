"""Crea o actualiza el esquema de la base de datos del subproyecto 'mapa'.

Uso:
    python scripts/mapa/init_db.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from src.logging_setup import configurar_logging  # noqa: E402
from src.mapa.config_mapa import CLIENTES_DB_PATH, PROVINCIAS_MAPA  # noqa: E402
from src.mapa.db import conexion, inicializar_esquema, listar_tablas  # noqa: E402

TABLAS_ESPERADAS = {"clientes"}


def main() -> int:
    log = configurar_logging("mapa_init_db")
    log.info("Inicializando esquema del mapa en %s", CLIENTES_DB_PATH)
    log.info("Provincias activas en V1: %s", ", ".join(PROVINCIAS_MAPA))

    with conexion() as conn:
        inicializar_esquema(conn)
        tablas = set(listar_tablas(conn))

    log.info("Tablas presentes: %s", ", ".join(sorted(tablas)) or "(ninguna)")

    faltan = TABLAS_ESPERADAS - tablas
    if faltan:
        log.error("Faltan tablas: %s", ", ".join(sorted(faltan)))
        return 1

    log.info("OK: esquema del mapa listo.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
