"""Estadísticas rápidas del estado de la base de datos.

Uso:
    python scripts/inspeccionar_db.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.db import conexion  # noqa: E402


def main() -> int:
    with conexion() as conn:
        total = conn.execute("SELECT COUNT(*) FROM licitaciones").fetchone()[0]
        pasan = conn.execute(
            "SELECT COUNT(*) FROM licitaciones WHERE pasa_filtros = 1"
        ).fetchone()[0]
        print(f"Total en DB:    {total}")
        print(f"Pasan filtros:  {pasan}")
        print()

        print("--- ID de candidatas (para usar con --licitacion en analizar_candidatas.py) ---")
        for f in conn.execute(
            "SELECT id, expediente, provincia, importe_sin_iva, cpv_principal, "
            "substr(objeto, 1, 65) as obj FROM licitaciones "
            "WHERE pasa_filtros = 1 ORDER BY importe_sin_iva ASC"
        ):
            prov = (f["provincia"] or "?")[:8]
            imp = f["importe_sin_iva"] or 0
            cpv = f["cpv_principal"] or "?"
            print(f"  id={f['id']:>5} | {prov:<8} | {imp:>9.0f} EUR | CPV {cpv:<8} | {f['obj']}")
        print()

        print("--- Top licitaciones que pasan filtros (por importe descendente) ---")
        filas = conn.execute(
            "SELECT expediente, provincia, importe_sin_iva, cpv_principal, "
            "fecha_limite_presentacion, substr(objeto, 1, 80) as obj "
            "FROM licitaciones WHERE pasa_filtros = 1 "
            "ORDER BY importe_sin_iva DESC LIMIT 15"
        ).fetchall()
        for f in filas:
            prov = (f["provincia"] or "?")[:10]
            imp = f["importe_sin_iva"] or 0
            cpv = f["cpv_principal"] or "?"
            fl = (f["fecha_limite_presentacion"] or "?")[:10]
            obj = f["obj"] or ""
            print(f"  {prov:10s} | {imp:>10.0f} EUR | CPV {cpv:8s} | hasta {fl} | {obj}")

        print()
        print("--- Motivos de descarte (top) ---")
        for f in conn.execute(
            "SELECT motivo_descarte, COUNT(*) as n FROM licitaciones "
            "WHERE pasa_filtros = 0 GROUP BY motivo_descarte "
            "ORDER BY n DESC LIMIT 15"
        ):
            print(f"  {f['n']:6d}  {f['motivo_descarte']}")

        print()
        print("--- Provincias andaluzas en DB (pasan filtros) ---")
        for f in conn.execute(
            "SELECT provincia, COUNT(*) as n FROM licitaciones "
            "WHERE pasa_filtros = 1 GROUP BY provincia ORDER BY n DESC"
        ):
            prov = f["provincia"] or "(sin provincia)"
            print(f"  {f['n']:6d}  {prov}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
