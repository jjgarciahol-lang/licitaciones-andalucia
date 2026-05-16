"""Backfill: descarga y parsea N meses de PLACSP e inserta todo en SQLite.

Uso:
    python scripts/backfill.py              # últimos 3 meses + mes actual
    python scripts/backfill.py --meses 6    # últimos 6 meses + actual
    python scripts/backfill.py --mes 2026 5 # un sólo mes concreto

El backfill es idempotente: si una licitación ya está, hace upsert correcto.
"""
from __future__ import annotations

import argparse
import json
import sys
import traceback
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.db import conexion  # noqa: E402
from src.ingesta.codice_parser import parsear_zip  # noqa: E402
from src.ingesta.persistencia import upsert_licitacion  # noqa: E402
from src.ingesta.placsp_downloader import descargar_mes, meses_a_procesar  # noqa: E402
from src.logging_setup import configurar_logging  # noqa: E402


def _registrar_log(conn, etapa: str, estado: str, **extra) -> int:
    fila = conn.execute(
        """INSERT INTO log_ejecuciones
           (etapa, estado, licitaciones_nuevas, licitaciones_actualizadas,
            licitaciones_pasan_filtros, error_mensaje, error_traceback, metadata)
           VALUES (?,?,?,?,?,?,?,?)""",
        (
            etapa, estado,
            extra.get("nuevas", 0),
            extra.get("actualizadas", 0),
            extra.get("pasan_filtros", 0),
            extra.get("error", None),
            extra.get("traceback", None),
            json.dumps(extra.get("metadata", {}), ensure_ascii=False),
        ),
    )
    return fila.lastrowid


def _cerrar_log(conn, log_id: int, **extra) -> None:
    ahora = datetime.now(timezone.utc).isoformat(timespec="seconds")
    sets = ["fecha_fin = ?"]
    valores: list = [ahora]
    for campo in (
        "licitaciones_nuevas", "licitaciones_actualizadas",
        "licitaciones_pasan_filtros", "error_mensaje", "error_traceback",
    ):
        if campo in extra:
            sets.append(f"{campo} = ?")
            valores.append(extra[campo])
    if "metadata" in extra:
        sets.append("metadata = ?")
        valores.append(json.dumps(extra["metadata"], ensure_ascii=False))
    if "estado" in extra:
        sets.append("estado = ?")
        valores.append(extra["estado"])
    valores.append(log_id)
    conn.execute(f"UPDATE log_ejecuciones SET {','.join(sets)} WHERE id = ?", valores)


def procesar_mes(conn, year: int, month: int, *, force_descarga: bool = False) -> dict:
    log = configurar_logging("backfill")
    es_mes_actual = (datetime.now().year, datetime.now().month) == (year, month)
    forzar = force_descarga or es_mes_actual

    log_id = _registrar_log(conn, etapa="ingesta", estado="en_curso",
                            metadata={"year": year, "month": month, "force": forzar})

    contadores: Counter = Counter()
    try:
        ruta_zip = descargar_mes(year, month, force=forzar)
        log.info("Procesando %s", ruta_zip.name)

        for licit in parsear_zip(ruta_zip, solo_pub=True):
            res = upsert_licitacion(conn, licit)
            contadores[res.estado] += 1
            if res.pasa_filtros:
                contadores["pasan_filtros"] += 1

            total = sum(v for k, v in contadores.items() if k != "pasan_filtros")
            if total > 0 and total % 1000 == 0:
                log.info("  procesadas %d licitaciones de %s...", total, ruta_zip.name)

        _cerrar_log(
            conn, log_id,
            estado="ok",
            licitaciones_nuevas=contadores["nueva"],
            licitaciones_actualizadas=(
                contadores["actualizada_relevante"] + contadores["actualizada_menor"]
            ),
            licitaciones_pasan_filtros=contadores["pasan_filtros"],
            metadata={"year": year, "month": month, "force": forzar,
                      "desglose": dict(contadores)},
        )
        log.info(
            "OK %04d-%02d  nuevas=%d  upd_rel=%d  upd_men=%d  sin_cambios=%d  pasan_filtros=%d",
            year, month,
            contadores["nueva"],
            contadores["actualizada_relevante"],
            contadores["actualizada_menor"],
            contadores["sin_cambios"],
            contadores["pasan_filtros"],
        )
        return dict(contadores)

    except Exception as e:
        tb = traceback.format_exc()
        log.exception("Fallo en %04d-%02d: %s", year, month, e)
        _cerrar_log(
            conn, log_id,
            estado="error",
            error_mensaje=str(e),
            error_traceback=tb,
            metadata={"year": year, "month": month},
        )
        raise


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--meses", type=int, default=3,
                   help="Meses anteriores a procesar (además del actual). Default: 3")
    p.add_argument("--mes", nargs=2, type=int, metavar=("YEAR", "MONTH"),
                   help="Procesa sólo un mes concreto")
    p.add_argument("--force", action="store_true",
                   help="Fuerza redescarga de ZIPs aunque estén en disco")
    args = p.parse_args()

    log = configurar_logging("backfill")
    meses = [tuple(args.mes)] if args.mes else meses_a_procesar(args.meses)
    log.info("Backfill de %d meses: %s",
             len(meses), ", ".join(f"{y}-{m:02d}" for y, m in meses))

    totales: Counter = Counter()
    with conexion() as conn:
        for y, m in meses:
            try:
                resultado = procesar_mes(conn, y, m, force_descarga=args.force)
            except Exception:
                log.error("Saltando %04d-%02d por error", y, m)
                continue
            for k, v in resultado.items():
                totales[k] += v

    print()
    print("=" * 60)
    print("RESUMEN BACKFILL")
    print("=" * 60)
    print(f"  Nuevas:                    {totales['nueva']}")
    print(f"  Actualizadas (relevante):  {totales['actualizada_relevante']}")
    print(f"  Actualizadas (menor):      {totales['actualizada_menor']}")
    print(f"  Sin cambios:               {totales['sin_cambios']}")
    print(f"  Pasan filtros:             {totales['pasan_filtros']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
