"""Analiza con Claude todas las licitaciones que pasan filtros y aún no
tienen análisis para la versión actual del prompt.

Uso:
    python scripts/analizar_candidatas.py                # todas las pendientes
    python scripts/analizar_candidatas.py --max 3        # sólo 3 (smoke test)
    python scripts/analizar_candidatas.py --licitacion ID # una concreta
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config import COSTE_MAX_DIARIO_USD  # noqa: E402
from src.db import conexion  # noqa: E402
from src.ia.analizador import (  # noqa: E402
    analizar_candidatas_pendientes,
    analizar_licitacion,
)
from src.logging_setup import configurar_logging  # noqa: E402


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--max", type=int, help="Máximo de licitaciones a analizar")
    p.add_argument("--licitacion", type=int, help="ID concreto a analizar")
    p.add_argument("--ignorar-coste", action="store_true",
                   help="No parar aunque se supere COSTE_MAX_DIARIO_USD")
    args = p.parse_args()

    log = configurar_logging("analizar_candidatas")
    coste_total = 0.0

    with conexion() as conn:
        if args.licitacion:
            resultado = analizar_licitacion(conn, args.licitacion)
            print(resultado)
            return 0

        for resultado in analizar_candidatas_pendientes(conn, max_licitaciones=args.max):
            coste = resultado.get("coste_usd", 0.0) or 0.0
            coste_total += coste
            log.info(
                "  %s (id=%s) -> %s score=%s coste=$%.4f (acum $%.4f)",
                resultado.get("expediente"),
                resultado.get("licitacion_id"),
                resultado.get("encaje"),
                resultado.get("score"),
                coste, coste_total,
            )
            if not args.ignorar_coste and coste_total >= COSTE_MAX_DIARIO_USD:
                log.warning("Coste acumulado $%.4f alcanza el techo $%s — paro.",
                            coste_total, COSTE_MAX_DIARIO_USD)
                break

    print(f"\nCoste total: $ {coste_total:.4f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
