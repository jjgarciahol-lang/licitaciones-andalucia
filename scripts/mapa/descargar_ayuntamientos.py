"""Ingesta de ayuntamientos: 45 municipios de Cádiz → tabla `clientes`.

Lista canónica derivada del propio CSV de centros de la Junta (todo municipio
tiene al menos un centro), y la sede física de cada ayuntamiento se geocodifica
con Nominatim respetando 1 req/s + cache local (data/mapa/geocache.db).

Uso:
    python scripts/mapa/descargar_ayuntamientos.py
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from src.logging_setup import configurar_logging  # noqa: E402
from src.mapa.config_mapa import PROVINCIAS_MAPA  # noqa: E402
from src.mapa.db import conexion  # noqa: E402
from src.mapa.ingesta_ayuntamientos import ingestar  # noqa: E402


def main() -> int:
    log = configurar_logging("mapa_ayuntamientos")
    log.info("Ingesta ayuntamientos — provincias activas: %s", ", ".join(PROVINCIAS_MAPA))
    t0 = time.monotonic()

    resumen = ingestar()
    duracion = time.monotonic() - t0

    log.info("Resumen ingesta ayuntamientos:")
    log.info("  municipios objetivo .......... %d", resumen["municipios"])
    log.info("  cache hits Nominatim ......... %d", resumen["cache_hits"])
    log.info("  geocodificados (con coords) .. %d", resumen["geocodificados"])
    log.info("  sin coordenadas .............. %d", resumen["sin_coordenadas"])
    log.info("  nuevos en DB ................. %d", resumen["nuevos"])
    log.info("  actualizados en DB ........... %d", resumen["actualizados"])
    log.info("  duracion total ............... %.1fs", duracion)

    with conexion() as conn:
        log.info("Top 5 sedes geocodificadas:")
        for fila in conn.execute(
            "SELECT nombre, direccion, lat, lon FROM clientes "
            "WHERE tipo='ayuntamiento' AND lat IS NOT NULL ORDER BY nombre LIMIT 5"
        ):
            log.info("  %-30s  %s  (%.5f, %.5f)",
                     fila["nombre"], (fila["direccion"] or "")[:60],
                     fila["lat"], fila["lon"])

        sin = list(conn.execute(
            "SELECT nombre FROM clientes WHERE tipo='ayuntamiento' AND lat IS NULL ORDER BY nombre"
        ))
        if sin:
            log.warning("Ayuntamientos SIN coordenadas (revisar manualmente):")
            for fila in sin:
                log.warning("  - %s", fila["nombre"])

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
