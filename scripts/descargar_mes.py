"""Descarga el ZIP de PLACSP de un mes concreto.

Uso:
    python scripts/descargar_mes.py 2026 5            # mes en curso
    python scripts/descargar_mes.py 2026 5 --force    # fuerza redescarga
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.ingesta.placsp_downloader import descargar_mes  # noqa: E402
from src.logging_setup import configurar_logging  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Descarga el ZIP mensual de PLACSP")
    parser.add_argument("year", type=int)
    parser.add_argument("month", type=int)
    parser.add_argument("--force", action="store_true", help="Fuerza redescarga aunque exista")
    args = parser.parse_args()

    log = configurar_logging("descargar_mes")
    log.info("Descargando %04d-%02d (force=%s)", args.year, args.month, args.force)

    try:
        ruta = descargar_mes(args.year, args.month, force=args.force)
    except Exception as e:
        log.exception("Fallo en la descarga: %s", e)
        return 1

    log.info("OK -> %s", ruta)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
