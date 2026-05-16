"""Exporta los datos de las licitaciones que pasan filtros como JS para el
mockup del dashboard. Imprime el bloque LICITACIONES = [...] listo para pegar.

Uso:
    python scripts/export_para_dashboard.py 4371 3304
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.db import conexion  # noqa: E402


def main() -> int:
    ids = [int(x) for x in sys.argv[1:]] if len(sys.argv) > 1 else None
    with conexion() as conn:
        if ids:
            placeholders = ",".join(["?"] * len(ids))
            filas = conn.execute(
                f"SELECT * FROM licitaciones WHERE id IN ({placeholders})",
                ids,
            ).fetchall()
        else:
            filas = conn.execute(
                "SELECT * FROM licitaciones WHERE pasa_filtros = 1 "
                "ORDER BY importe_sin_iva DESC"
            ).fetchall()

        salida = []
        for f in filas:
            docs = json.loads(f["documentos_urls"]) if f["documentos_urls"] else []
            salida.append({
                "id": f["id"],
                "expediente": f["expediente"],
                "organo": f["organo_contratacion"],
                "provincia": f["provincia"],
                "municipio": f["municipio"],
                "importe_sin_iva": f["importe_sin_iva"],
                "importe_con_iva": f["importe_con_iva"],
                "fecha_publicacion": f["fecha_publicacion"],
                "fecha_limite": f["fecha_limite_presentacion"],
                "cpv": f["cpv_principal"],
                "objeto": f["objeto"],
                "enlace_placsp": f["enlace_placsp"],
                "pliegos_urls": [
                    {"tipo": d.get("tipo"), "url": d.get("url"), "nombre": d.get("nombre")}
                    for d in docs if d.get("tipo") != "PCAP"
                ],
            })
    print(json.dumps(salida, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
