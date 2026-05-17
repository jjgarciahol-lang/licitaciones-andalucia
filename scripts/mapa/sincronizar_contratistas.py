"""Sincroniza contratistas locales desde licitaciones.db al mapa.

Lee la tabla `adjudicaciones` (poblada por scripts/extraer_adjudicaciones.py),
agrupa por NIF, geocodifica el municipio de cada empresa con Nominatim, y
persiste como tipo='contratista_local' en clientes.db (mapa).

Uso:
    python scripts/mapa/sincronizar_contratistas.py
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from src.logging_setup import configurar_logging  # noqa: E402
from src.mapa.config_mapa import PROVINCIAS_MAPA  # noqa: E402
from src.mapa.db import conexion  # noqa: E402
from src.mapa.ingesta_contratistas import ingestar  # noqa: E402


def main() -> int:
    log = configurar_logging("mapa_contratistas")
    log.info("Sincronizando contratistas locales — provincias: %s",
             ", ".join(PROVINCIAS_MAPA))
    t0 = time.monotonic()

    r = ingestar()
    duracion = time.monotonic() - t0

    log.info("Resumen:")
    log.info("  contratistas leidos ..... %d", r["contratistas_leidos"])
    log.info("  municipios distintos .... %d", r["municipios_unicos"])
    log.info("  geocodificados .......... %d", r["geocoded"])
    log.info("  sin ciudad .............. %d", r["sin_ciudad"])
    log.info("  sin coords (geocoder) ... %d", r["sin_coords"])
    log.info("  nuevos en DB ............ %d", r["nuevos"])
    log.info("  actualizados ............ %d", r["actualizados"])
    log.info("  duracion ................ %.1fs", duracion)

    with conexion() as conn:
        log.info("Top 10 contratistas locales por nº de adjudicaciones:")
        for fila in conn.execute(
            "SELECT nombre, direccion FROM clientes "
            "WHERE tipo='contratista_local' ORDER BY id LIMIT 10"
        ):
            log.info("  %-50s %s", (fila["nombre"] or "")[:50], (fila["direccion"] or "")[:80])

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
