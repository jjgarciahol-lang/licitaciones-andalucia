"""Ingesta de centros docentes de la Junta de Andalucía → tabla `clientes`.

Uso:
    python scripts/mapa/descargar_centros.py            # usa CSV cacheado si existe
    python scripts/mapa/descargar_centros.py --force    # vuelve a descargar el CSV
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from src.logging_setup import configurar_logging  # noqa: E402
from src.mapa.config_mapa import PROVINCIAS_MAPA  # noqa: E402
from src.mapa.db import conexion  # noqa: E402
from src.mapa.ingesta_centros import ingestar  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--force", action="store_true",
                        help="Vuelve a descargar el CSV aunque esté cacheado")
    args = parser.parse_args()

    log = configurar_logging("mapa_centros")
    log.info("Ingesta centros — provincias activas: %s", ", ".join(PROVINCIAS_MAPA))

    resumen = ingestar(force_download=args.force)

    log.info("Resumen ingesta centros:")
    log.info("  filas leídas del CSV ......... %d", resumen["leidos"])
    log.info("  descartadas (otra provincia) . %d", resumen["fuera_provincia"])
    log.info("  descartadas (sin código) ..... %d", resumen["sin_codigo"])
    log.info("  nuevas en DB ................. %d", resumen["nuevos"])
    log.info("  actualizadas en DB ........... %d", resumen["actualizados"])
    log.info("  sin coordenadas en origen .... %d", resumen["sin_coordenadas"])

    # Desglose por tipo y municipio para validación a ojo
    with conexion() as conn:
        log.info("Desglose por tipo:")
        for fila in conn.execute(
            "SELECT tipo, COUNT(*) c FROM clientes WHERE fuente='junta_andalucia' "
            "GROUP BY tipo ORDER BY c DESC"
        ):
            log.info("  %-22s %d", fila["tipo"], fila["c"])

        log.info("Top 10 municipios:")
        for fila in conn.execute(
            "SELECT municipio, COUNT(*) c FROM clientes WHERE fuente='junta_andalucia' "
            "GROUP BY municipio ORDER BY c DESC LIMIT 10"
        ):
            log.info("  %-30s %d", fila["municipio"] or "(sin municipio)", fila["c"])

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
