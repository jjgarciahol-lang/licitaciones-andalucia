"""Reaplica filtros sobre todas las licitaciones de la DB.

Útil cuando cambian las provincias permitidas, los CPVs relevantes o los
umbrales de importe — no requiere re-ingesta.

Uso: python scripts/reaplicar_filtros.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.db import conexion  # noqa: E402
from src.ingesta.persistencia import reaplicar_filtros  # noqa: E402
from src.logging_setup import configurar_logging  # noqa: E402


def main() -> int:
    log = configurar_logging("reaplicar_filtros")
    with conexion() as conn:
        stats = reaplicar_filtros(conn)
    log.info("Resultado: %s", stats)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
