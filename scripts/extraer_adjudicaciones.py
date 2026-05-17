"""Extrae adjudicatarios de los ZIPs históricos de PLACSP a la tabla `adjudicaciones`.

Itera todos los ZIPs en `data/zips/` (los que ya tiene descargados el
pipeline diario) y extrae cada `<cac:TenderResult>` con su WinningParty.

No depende del estado de la licitación: las adjudicaciones aparecen en
estados ADJ/RES/etc., no PUB, así que el filtro habitual del parser
principal las excluye. Este script vive aparte y es el motor del
subproyecto mapa (capa contratista_local).

Uso:
    python scripts/extraer_adjudicaciones.py
    python scripts/extraer_adjudicaciones.py --solo-zip data/zips/licitacionesPerfilesContratanteCompleto3_202605.zip
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config import ZIPS_DIR  # noqa: E402
from src.db import conexion  # noqa: E402
from src.ingesta.adjudicaciones import parsear_zip, upsert_adjudicacion  # noqa: E402
from src.logging_setup import configurar_logging  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--solo-zip", help="Procesar un solo ZIP específico")
    args = parser.parse_args()

    log = configurar_logging("extraer_adjudicaciones")

    if args.solo_zip:
        zips = [Path(args.solo_zip)]
    else:
        zips = sorted(Path(ZIPS_DIR).glob("*.zip"))
    log.info("Procesando %d ZIPs", len(zips))

    total_nuevas = 0
    total_actualizadas = 0
    total_zips_procesados = 0
    t0 = time.monotonic()

    with conexion() as conn:
        for zip_path in zips:
            log.info("Procesando %s", zip_path.name)
            nuevas = actualizadas = 0
            conn.execute("BEGIN")
            try:
                for adj in parsear_zip(zip_path):
                    resultado = upsert_adjudicacion(conn, adj)
                    if resultado == "nueva":
                        nuevas += 1
                    else:
                        actualizadas += 1
                conn.execute("COMMIT")
            except Exception as e:
                conn.execute("ROLLBACK")
                log.error("Error procesando %s: %s", zip_path.name, e)
                continue
            log.info("   nuevas: %d  actualizadas: %d", nuevas, actualizadas)
            total_nuevas += nuevas
            total_actualizadas += actualizadas
            total_zips_procesados += 1

    duracion = time.monotonic() - t0
    log.info("Resumen:")
    log.info("  ZIPs procesados ......... %d", total_zips_procesados)
    log.info("  adjudicaciones nuevas ... %d", total_nuevas)
    log.info("  actualizadas ............ %d", total_actualizadas)
    log.info("  duración ................ %.1fs", duracion)

    with conexion() as conn:
        total = conn.execute("SELECT COUNT(*) FROM adjudicaciones").fetchone()[0]
        unicos = conn.execute(
            "SELECT COUNT(DISTINCT nif) FROM adjudicaciones WHERE nif IS NOT NULL"
        ).fetchone()[0]
        andalucia = conn.execute(
            "SELECT COUNT(*) FROM adjudicaciones WHERE provincia IN "
            "('Cádiz','Sevilla','Málaga','Granada','Huelva','Jaén','Córdoba','Almería')"
        ).fetchone()[0]
        log.info("  total en DB ............. %d adjudicaciones", total)
        log.info("  NIFs únicos ............. %d empresas distintas", unicos)
        log.info("  en Andalucía ............ %d (%.0f%%)",
                 andalucia, 100 * andalucia / total if total else 0)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
