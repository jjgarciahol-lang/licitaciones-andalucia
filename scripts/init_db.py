"""Crea o actualiza el esquema de la base de datos SQLite.

Uso:
    python scripts/init_db.py
"""
from __future__ import annotations

import sys
from pathlib import Path

# Permite ejecutar el script directamente: añade la raíz del proyecto al sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config import DB_PATH  # noqa: E402
from src.db import conexion, inicializar_esquema, listar_tablas  # noqa: E402
from src.logging_setup import configurar_logging  # noqa: E402

TABLAS_ESPERADAS = {"licitaciones", "pliegos_descargados", "analisis_ia", "log_ejecuciones"}


def main() -> int:
    log = configurar_logging("init_db")
    log.info("Inicializando esquema en %s", DB_PATH)

    with conexion() as conn:
        inicializar_esquema(conn)
        tablas = set(listar_tablas(conn))

    log.info("Tablas presentes: %s", ", ".join(sorted(tablas)) or "(ninguna)")

    faltan = TABLAS_ESPERADAS - tablas
    if faltan:
        log.error("Faltan tablas: %s", ", ".join(sorted(faltan)))
        return 1

    log.info("OK: las 4 tablas requeridas están presentes.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
