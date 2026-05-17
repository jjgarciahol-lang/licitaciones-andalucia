"""Ingesta de campings desde OpenStreetMap.

Uso:
    python scripts/mapa/descargar_osm.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from src.logging_setup import configurar_logging  # noqa: E402
from src.mapa.config_mapa import PROVINCIAS_MAPA  # noqa: E402
from src.mapa.db import conexion  # noqa: E402
from src.mapa.ingesta_osm import ingestar_campings  # noqa: E402


def main() -> int:
    log = configurar_logging("mapa_osm")
    log.info("Ingesta OSM — provincias activas: %s", ", ".join(PROVINCIAS_MAPA))

    log.info("== Campings ==")
    r = ingestar_campings()
    log.info("  leidos: %d  nuevos: %d  actualizados: %d  fuera_bbox: %d  sin_coords: %d",
             r["leidos"], r["nuevos"], r["actualizados"], r["fuera_bbox"], r["sin_coords"])

    with conexion() as conn:
        log.info("Totales en DB por tipo:")
        for fila in conn.execute(
            "SELECT tipo, COUNT(*) c FROM clientes GROUP BY tipo ORDER BY c DESC"
        ):
            log.info("  %-20s %d", fila["tipo"], fila["c"])

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
