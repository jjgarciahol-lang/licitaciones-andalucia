"""Sincroniza contratistas locales y competencia desde licitaciones.db al mapa.

Lee la tabla `adjudicaciones` (poblada por scripts/extraer_adjudicaciones.py)
y genera dos capas:

- `contratista_local`: empresas con sede en provincias activas del mapa
  (clientes potenciales como proveedor de obra).
- `competencia`: empresas (de cualquier sede) que han ganado licitaciones con
  CPVs del catálogo Higiofi en Andalucía (competidores reales del negocio).

Uso:
    python scripts/mapa/sincronizar_contratistas.py
    python scripts/mapa/sincronizar_contratistas.py --solo contratistas
    python scripts/mapa/sincronizar_contratistas.py --solo competencia
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from src.logging_setup import configurar_logging  # noqa: E402
from src.mapa.config_mapa import PROVINCIAS_MAPA  # noqa: E402
from src.mapa.db import conexion  # noqa: E402
from src.mapa.ingesta_contratistas import ingestar, ingestar_competencia_higiofi  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--solo", choices=("contratistas", "competencia"),
                        help="Limita a una sola capa")
    args = parser.parse_args()

    log = configurar_logging("mapa_contratistas")
    log.info("Sincronizando desde licitaciones.db — provincias activas: %s",
             ", ".join(PROVINCIAS_MAPA))
    t0 = time.monotonic()

    if args.solo in (None, "contratistas"):
        log.info("== Contratistas locales (sede en provincias activas) ==")
        r = ingestar()
        log.info("  leidos: %d  geocodificados: %d  nuevos: %d  sin_coords: %d",
                 r["contratistas_leidos"], r["geocoded"], r["nuevos"], r["sin_coords"])

    if args.solo in (None, "competencia"):
        log.info("== Competencia Higiofi (CPVs del catálogo, ganadas en Andalucía) ==")
        r = ingestar_competencia_higiofi()
        log.info("  leidos: %d  municipios: %d  nuevos: %d  sin_coords: %d  sin_ciudad: %d",
                 r["empresas_leidas"], r["municipios_unicos"],
                 r["nuevos"], r["sin_coords"], r["sin_ciudad"])

    log.info("Duración total: %.1fs", time.monotonic() - t0)

    with conexion() as conn:
        log.info("Top 10 competidores Higiofi:")
        for fila in conn.execute(
            "SELECT nombre, direccion, municipio, provincia FROM clientes "
            "WHERE tipo='competencia' ORDER BY id LIMIT 10"
        ):
            log.info("  %-45s  %s",
                     (fila["nombre"] or "")[:45],
                     (fila["direccion"] or "")[:90])

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
