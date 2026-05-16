"""Descarga pliegos y extrae texto de TODAS las licitaciones que pasan filtros.

Idempotente: si un PDF ya está descargado y extraído, no repite trabajo.

Uso: python scripts/bajar_todas_pliegos.py
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Bajamos verbosity del descargador (los warnings son URLs gigantes)
logging.getLogger("src.pliegos.descargador").setLevel(logging.ERROR)

from src.db import conexion  # noqa: E402
from src.db_utils import cerrar_ejecuciones_huerfanas  # noqa: E402
from src.logging_setup import configurar_logging  # noqa: E402
from src.pliegos.descargador import descargar_pliegos_de_licitacion  # noqa: E402
from src.pliegos.extractor import extraer_y_persistir  # noqa: E402


def main() -> int:
    log = configurar_logging("bajar_todas")
    with conexion() as conn:
        cerrar_ejecuciones_huerfanas(conn)

        candidatas = conn.execute(
            "SELECT id, expediente, importe_sin_iva, substr(objeto, 1, 60) as obj "
            "FROM licitaciones WHERE pasa_filtros = 1 "
            "ORDER BY importe_sin_iva DESC"
        ).fetchall()
        log.info("Procesando %d candidatas", len(candidatas))

        ok_descarga, fail_descarga = 0, 0
        ok_extract, fail_extract = 0, 0

        for c in candidatas:
            resultados = descargar_pliegos_de_licitacion(conn, c["id"])
            descargados = [r for r in resultados if r["estado"] in ("descargado", "ya_existia")]
            if descargados:
                ok_descarga += 1
            else:
                fail_descarga += 1
                log.info("  FAIL id=%5d (%9.0f€) %s", c["id"], c["importe_sin_iva"] or 0, c["obj"])
                continue

            for r in descargados:
                if not r.get("ruta_local"):
                    continue
                pdf = Path(r["ruta_local"])
                res = extraer_y_persistir(conn, c["id"], pdf)
                if res.ok:
                    ok_extract += 1
                else:
                    fail_extract += 1
                    log.debug("    Extracción fallida en %s: %s", pdf.name, res.error)

            log.info("  OK   id=%5d (%9.0f€) %d/%d pliegos - %s",
                     c["id"], c["importe_sin_iva"] or 0,
                     len(descargados), len(resultados), c["obj"])

        log.info("Resumen: %d con pliegos · %d sin pliegos · extracciones %d OK / %d fallo",
                 ok_descarga, fail_descarga, ok_extract, fail_extract)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
