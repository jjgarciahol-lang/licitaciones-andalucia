"""Descarga pliegos de licitaciones concretas y extrae su texto.

Útil para inspección manual sin tocar la API de Anthropic.

Uso:
    python scripts/descargar_y_extraer.py 1519
    python scripts/descargar_y_extraer.py 1519 4750
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.db import conexion  # noqa: E402
from src.logging_setup import configurar_logging  # noqa: E402
from src.pliegos.descargador import descargar_pliegos_de_licitacion  # noqa: E402
from src.pliegos.extractor import extraer_y_persistir  # noqa: E402


def procesar(lic_id: int) -> None:
    log = configurar_logging("descargar_y_extraer")
    with conexion() as conn:
        fila = conn.execute(
            "SELECT expediente, objeto FROM licitaciones WHERE id = ?", (lic_id,)
        ).fetchone()
        if fila is None:
            log.error("Licitación %s no existe", lic_id)
            return
        log.info("=== Licitación id=%s expediente=%s ===", lic_id, fila["expediente"])
        log.info("    Objeto: %s", (fila["objeto"] or "")[:120])

        resultados = descargar_pliegos_de_licitacion(conn, lic_id)
        log.info("    Pliegos: %s", [r["estado"] for r in resultados])

        for r in resultados:
            if not r.get("ruta_local"):
                continue
            pdf = Path(r["ruta_local"])
            res = extraer_y_persistir(conn, lic_id, pdf)
            if res.ok:
                log.info("    %s -> %d páginas, %d chars -> %s",
                         pdf.name, res.paginas, len(res.texto), res.ruta_txt.name)
            else:
                log.warning("    %s -> error: %s", pdf.name, res.error)


def main() -> int:
    if len(sys.argv) < 2:
        print("Uso: python scripts/descargar_y_extraer.py ID [ID ...]")
        return 1
    for arg in sys.argv[1:]:
        procesar(int(arg))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
