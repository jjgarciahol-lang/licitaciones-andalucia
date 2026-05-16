"""Genera dashboard/index.html a partir del template y la base de datos.

Toma `dashboard/template.html`, reemplaza los placeholders `__LICITACIONES_JSON__`
y `__META_JSON__` por los datos reales y escribe `dashboard/index.html`.

Las licitaciones incluidas:
- Pasan filtros (pasa_filtros = 1)
- Fecha límite todavía futura
- Más el último análisis IA si existe (deja `analisis` = null si no se ha
  procesado todavía con Claude — útil mientras estamos en modo Max manual).

Uso: python scripts/generar_dashboard.py
"""
from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config import PROJECT_ROOT  # noqa: E402
from src.db import conexion  # noqa: E402
from src.logging_setup import configurar_logging  # noqa: E402

TEMPLATE_PATH = PROJECT_ROOT / "dashboard" / "template.html"
OUTPUT_PATH   = PROJECT_ROOT / "dashboard" / "index.html"


SQL_CANDIDATAS = """
SELECT l.id, l.expediente, l.organo_contratacion, l.provincia, l.municipio,
       l.importe_sin_iva, l.importe_con_iva, l.fecha_publicacion,
       l.fecha_limite_presentacion, l.cpv_principal, l.objeto, l.enlace_placsp,
       l.documentos_urls
FROM licitaciones l
WHERE l.pasa_filtros = 1
  AND (l.fecha_limite_presentacion IS NULL
       OR l.fecha_limite_presentacion > datetime('now'))
ORDER BY l.importe_sin_iva DESC
"""

SQL_ULTIMO_ANALISIS = """
SELECT encaje, encaje_score, motivo, resumen,
       productos_aplicables, requisitos_criticos, banderas_rojas
FROM analisis_ia
WHERE licitacion_id = ?
  AND encaje IS NOT NULL
ORDER BY fecha_analisis DESC
LIMIT 1
"""


def _docs_filtrados(docs_json: str | None) -> list[dict]:
    """Filtra documentos: descarta PCAP (decisión de proyecto) y los que no
    sean PDF descargable. Devuelve lista de {tipo, url, nombre}."""
    if not docs_json:
        return []
    try:
        docs = json.loads(docs_json)
    except json.JSONDecodeError:
        return []
    salida = []
    for d in docs:
        tipo = d.get("tipo")
        if tipo == "PCAP":
            continue
        url = d.get("url")
        if not url:
            continue
        salida.append({"tipo": tipo, "url": url, "nombre": d.get("nombre")})
    return salida


def _parsear_json_list(s: str | None) -> list:
    if not s:
        return []
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        return []


def construir_payload() -> tuple[list[dict], dict]:
    log = configurar_logging("generar_dashboard")
    licitaciones: list[dict] = []
    with conexion() as conn:
        total_db = conn.execute("SELECT COUNT(*) FROM licitaciones").fetchone()[0]
        filas = conn.execute(SQL_CANDIDATAS).fetchall()
        log.info("Candidatas vivas: %d (de %d en DB)", len(filas), total_db)

        for f in filas:
            analisis_fila = conn.execute(SQL_ULTIMO_ANALISIS, (f["id"],)).fetchone()
            if analisis_fila:
                analisis = {
                    "encaje": analisis_fila["encaje"],
                    "encaje_score": analisis_fila["encaje_score"],
                    "motivo": analisis_fila["motivo"] or "",
                    "resumen": analisis_fila["resumen"] or "",
                    "productos_aplicables": _parsear_json_list(analisis_fila["productos_aplicables"]),
                    "requisitos_criticos":  _parsear_json_list(analisis_fila["requisitos_criticos"]),
                    "banderas_rojas":       _parsear_json_list(analisis_fila["banderas_rojas"]),
                }
            else:
                analisis = None

            licitaciones.append({
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
                "pliegos_urls": _docs_filtrados(f["documentos_urls"]),
                "analisis": analisis,
            })

    meta = {
        "generado_en": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "total_db": total_db,
        "total_candidatas": len(licitaciones),
        "con_analisis": sum(1 for l in licitaciones if l["analisis"] is not None),
    }
    return licitaciones, meta


def inyectar(template: str, licitaciones: list[dict], meta: dict) -> str:
    json_licitaciones = json.dumps(licitaciones, ensure_ascii=False, indent=2)
    json_meta = json.dumps(meta, ensure_ascii=False)
    # Reemplazo de los placeholders. El template los tiene como:
    #   const LICITACIONES = /*__LICITACIONES_JSON__*/ [];
    # tras la sustitución queda:
    #   const LICITACIONES = [ {...}, {...} ];
    salida = re.sub(
        r"/\*__LICITACIONES_JSON__\*/\s*\[\]",
        json_licitaciones,
        template,
        count=1,
    )
    salida = re.sub(
        r"/\*__META_JSON__\*/\s*\{[^}]*\}",
        json_meta,
        salida,
        count=1,
    )
    return salida


def main() -> int:
    log = configurar_logging("generar_dashboard")
    if not TEMPLATE_PATH.exists():
        log.error("Falta el template: %s", TEMPLATE_PATH)
        return 1

    template = TEMPLATE_PATH.read_text(encoding="utf-8")
    licitaciones, meta = construir_payload()
    final = inyectar(template, licitaciones, meta)
    OUTPUT_PATH.write_text(final, encoding="utf-8")
    log.info("Escrito %s (%d candidatas, %d con análisis IA)",
             OUTPUT_PATH, meta["total_candidatas"], meta["con_analisis"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
