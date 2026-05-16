"""Carga análisis generados manualmente (modo Claude Code Max) en la tabla `analisis_ia`.

Lee un fichero JSON con esta estructura:

[
  {
    "licitacion_id": 4371,
    "encaje": "alto",
    "encaje_score": 92,
    "motivo": "...",
    "resumen": "...",
    "productos_aplicables": [...],
    "requisitos_criticos": [...],
    "banderas_rojas": [...]
  },
  ...
]

Uso:
    python scripts/cargar_analisis_manual.py data/analisis_pendientes.json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config import PROMPT_VERSION  # noqa: E402
from src.db import conexion  # noqa: E402
from src.logging_setup import configurar_logging  # noqa: E402

MODELO_ETIQUETA = "claude-code-max-manual"


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("fichero", type=Path, help="Ruta al JSON con los análisis")
    p.add_argument("--sobreescribir", action="store_true",
                   help="Si ya hay análisis para esa licitación con la misma prompt_version, lo reemplaza")
    args = p.parse_args()

    log = configurar_logging("cargar_analisis_manual")
    if not args.fichero.exists():
        log.error("No existe %s", args.fichero)
        return 1

    datos = json.loads(args.fichero.read_text(encoding="utf-8"))
    if not isinstance(datos, list):
        log.error("El JSON debe ser una lista de análisis")
        return 1

    insertados = 0
    saltados = 0
    actualizados = 0

    with conexion() as conn:
        for d in datos:
            lic_id = d.get("licitacion_id")
            if not lic_id:
                log.warning("Entrada sin licitacion_id: %s", d)
                continue

            existe = conn.execute(
                "SELECT id FROM analisis_ia WHERE licitacion_id = ? AND prompt_version = ?",
                (lic_id, PROMPT_VERSION),
            ).fetchone()

            if existe and not args.sobreescribir:
                log.info("  saltado id=%s (ya hay análisis para prompt %s)", lic_id, PROMPT_VERSION)
                saltados += 1
                continue

            valores = (
                lic_id, MODELO_ETIQUETA, PROMPT_VERSION,
                d.get("encaje"),
                d.get("encaje_score"),
                d.get("motivo"),
                d.get("resumen"),
                json.dumps(d.get("productos_aplicables", []), ensure_ascii=False),
                json.dumps(d.get("requisitos_criticos", []), ensure_ascii=False),
                json.dumps(d.get("banderas_rojas", []), ensure_ascii=False),
                json.dumps(d, ensure_ascii=False),
                0, 0, 0.0,  # tokens/coste = 0 (manual)
            )

            if existe and args.sobreescribir:
                conn.execute("DELETE FROM analisis_ia WHERE id = ?", (existe["id"],))
                actualizados += 1
            else:
                insertados += 1

            conn.execute(
                """INSERT INTO analisis_ia
                   (licitacion_id, modelo, prompt_version, encaje, encaje_score,
                    motivo, resumen, productos_aplicables, requisitos_criticos,
                    banderas_rojas, respuesta_raw,
                    tokens_entrada, tokens_salida, coste_usd)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                valores,
            )

    log.info("Total: %d insertados, %d actualizados, %d saltados",
             insertados, actualizados, saltados)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
