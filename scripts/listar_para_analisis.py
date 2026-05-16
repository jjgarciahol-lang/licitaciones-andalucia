"""Imprime, para cada candidata viva, sus datos clave y la ruta del .txt principal.

Útil para análisis manual: yo (Claude Code) consulto esta lista, leo cada .txt
y genero el JSON de análisis.

Uso:
    python scripts/listar_para_analisis.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config import PROMPT_VERSION  # noqa: E402
from src.db import conexion  # noqa: E402


def main() -> int:
    salida = []
    with conexion() as conn:
        # Candidatas sin análisis IA todavía
        filas = conn.execute(
            """SELECT l.id, l.expediente, l.objeto, l.organo_contratacion,
                      l.provincia, l.municipio, l.importe_sin_iva,
                      l.fecha_limite_presentacion, l.cpv_principal,
                      l.enlace_placsp
               FROM licitaciones l
               WHERE l.pasa_filtros = 1
                 AND l.fecha_limite_presentacion > datetime('now')
                 AND NOT EXISTS (
                    SELECT 1 FROM analisis_ia a
                    WHERE a.licitacion_id = l.id AND a.prompt_version = ?
                 )
               ORDER BY l.importe_sin_iva DESC""",
            (PROMPT_VERSION,),
        ).fetchall()

        for f in filas:
            pliegos = conn.execute(
                """SELECT tipo_documento, ruta_local, texto_extraido_path, paginas, extraccion_ok
                   FROM pliegos_descargados
                   WHERE licitacion_id = ? AND ruta_local IS NOT NULL
                   ORDER BY CASE tipo_documento
                              WHEN 'PPT' THEN 1
                              WHEN 'anexo' THEN 2
                              ELSE 3 END""",
                (f["id"],),
            ).fetchall()

            txts_ok = [
                {"tipo": p["tipo_documento"], "txt": p["texto_extraido_path"], "paginas": p["paginas"]}
                for p in pliegos if p["extraccion_ok"] == 1 and p["texto_extraido_path"]
            ]

            salida.append({
                "id": f["id"],
                "expediente": f["expediente"],
                "organo": f["organo_contratacion"],
                "provincia": f["provincia"],
                "municipio": f["municipio"],
                "importe_sin_iva": f["importe_sin_iva"],
                "fecha_limite": f["fecha_limite_presentacion"],
                "cpv": f["cpv_principal"],
                "objeto": f["objeto"],
                "enlace_placsp": f["enlace_placsp"],
                "txts": txts_ok,
            })

    print(json.dumps(salida, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
