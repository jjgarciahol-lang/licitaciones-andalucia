"""Auditoría rápida del estado del sistema: DB, pliegos, logs, código.

Uso: python scripts/auditoria.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config import (  # noqa: E402
    DB_PATH, PLIEGOS_DIR, ZIPS_DIR, PROJECT_ROOT,
    ANTHROPIC_API_KEY, AIRTABLE_API_KEY, RESEND_API_KEY,
    CPVS_RELEVANTES, PROVINCIAS_PERMITIDAS,
)
from src.db import conexion  # noqa: E402


def fmt_n(n):
    return f"{n:>6,}".replace(",", ".") if n is not None else "  ?"


def main() -> int:
    print("=" * 72)
    print(f" AUDITORÍA — {PROJECT_ROOT.name}")
    print("=" * 72)

    print("\n## 1. Configuración")
    print(f"  DB:         {DB_PATH}   ({'existe' if DB_PATH.exists() else 'NO EXISTE'})")
    print(f"  ZIPs dir:   {ZIPS_DIR}")
    print(f"  Pliegos:    {PLIEGOS_DIR}")
    print(f"  CPVs cargados:        {len(CPVS_RELEVANTES)} prefijos")
    print(f"  Provincias permitidas: {len(PROVINCIAS_PERMITIDAS)}")
    print(f"  ANTHROPIC_API_KEY:    {'CONFIGURADA' if ANTHROPIC_API_KEY else 'no configurada (OK — modo Max manual)'}")
    print(f"  AIRTABLE_API_KEY:     {'CONFIGURADA' if AIRTABLE_API_KEY else 'no configurada'}")
    print(f"  RESEND_API_KEY:       {'CONFIGURADA' if RESEND_API_KEY else 'no configurada'}")

    catalog_txt = PROJECT_ROOT / "prompts" / "catalogo_empresa.txt"
    catalog_pdf = PROJECT_ROOT / "prompts" / "catalogo_empresa.pdf"
    print(f"  Catálogo PDF: {'OK ' + str(catalog_pdf.stat().st_size // 1024) + ' KB' if catalog_pdf.exists() else 'NO'}")
    print(f"  Catálogo TXT: {'OK ' + str(catalog_txt.stat().st_size // 1024) + ' KB' if catalog_txt.exists() else 'NO'}")

    print("\n## 2. Base de datos")
    with conexion() as conn:
        total = conn.execute("SELECT COUNT(*) FROM licitaciones").fetchone()[0]
        pasan = conn.execute("SELECT COUNT(*) FROM licitaciones WHERE pasa_filtros = 1").fetchone()[0]
        no_pasan = conn.execute("SELECT COUNT(*) FROM licitaciones WHERE pasa_filtros = 0").fetchone()[0]
        pendientes_filtro = conn.execute("SELECT COUNT(*) FROM licitaciones WHERE pasa_filtros IS NULL").fetchone()[0]
        print(f"  Licitaciones totales:        {fmt_n(total)}")
        print(f"     pasan filtros:            {fmt_n(pasan)}")
        print(f"     descartadas por filtros:  {fmt_n(no_pasan)}")
        print(f"     sin filtrar:              {fmt_n(pendientes_filtro)}")

        # Pliegos
        pliegos_total = conn.execute("SELECT COUNT(*) FROM pliegos_descargados").fetchone()[0]
        pliegos_ok = conn.execute("SELECT COUNT(*) FROM pliegos_descargados WHERE ruta_local IS NOT NULL").fetchone()[0]
        pliegos_err = conn.execute("SELECT COUNT(*) FROM pliegos_descargados WHERE error IS NOT NULL").fetchone()[0]
        pliegos_ext_ok = conn.execute("SELECT COUNT(*) FROM pliegos_descargados WHERE extraccion_ok = 1").fetchone()[0]
        print(f"\n  Pliegos descargados:         {fmt_n(pliegos_total)}")
        print(f"     descargados OK:           {fmt_n(pliegos_ok)}")
        print(f"     con error:                {fmt_n(pliegos_err)}")
        print(f"     texto extraído:           {fmt_n(pliegos_ext_ok)}")

        # Análisis IA
        analisis_total = conn.execute("SELECT COUNT(*) FROM analisis_ia").fetchone()[0]
        print(f"\n  Análisis IA en DB:           {fmt_n(analisis_total)}")

        # Candidatas activas: pasan filtros, fecha límite futura, sin descartar
        candidatas_vivas = conn.execute(
            "SELECT COUNT(*) FROM licitaciones "
            "WHERE pasa_filtros = 1 AND fecha_limite_presentacion > datetime('now')"
        ).fetchone()[0]
        candidatas_sin_pliegos = conn.execute(
            "SELECT COUNT(*) FROM licitaciones l "
            "WHERE l.pasa_filtros = 1 AND l.fecha_limite_presentacion > datetime('now') "
            "AND NOT EXISTS (SELECT 1 FROM pliegos_descargados p "
            "                WHERE p.licitacion_id = l.id AND p.ruta_local IS NOT NULL)"
        ).fetchone()[0]
        candidatas_sin_analisis = conn.execute(
            "SELECT COUNT(*) FROM licitaciones l "
            "WHERE l.pasa_filtros = 1 AND l.fecha_limite_presentacion > datetime('now') "
            "AND NOT EXISTS (SELECT 1 FROM analisis_ia a WHERE a.licitacion_id = l.id)"
        ).fetchone()[0]
        print(f"\n  Candidatas vivas (pasan + plazo futuro): {fmt_n(candidatas_vivas)}")
        print(f"     sin pliegos descargables:                {fmt_n(candidatas_sin_pliegos)}")
        print(f"     sin análisis IA:                         {fmt_n(candidatas_sin_analisis)}")

        # Log ejecuciones
        ejecs = conn.execute(
            "SELECT etapa, estado, COUNT(*) as n FROM log_ejecuciones "
            "GROUP BY etapa, estado ORDER BY etapa, estado"
        ).fetchall()
        print(f"\n  Ejecuciones registradas:")
        for e in ejecs:
            print(f"     {e['etapa']:<12s} {e['estado']:<10s} {e['n']}")

    print("\n## 3. Ficheros en disco")
    # ZIPs
    zips = list(ZIPS_DIR.glob("*.zip"))
    print(f"  ZIPs PLACSP:        {len(zips)} ({sum(z.stat().st_size for z in zips) // 1_048_576} MB)")
    # PDFs descargados
    pdfs = list(PLIEGOS_DIR.rglob("*.pdf"))
    print(f"  PDFs pliegos:       {len(pdfs)} ({sum(p.stat().st_size for p in pdfs) // 1_048_576} MB)")
    # Textos extraídos
    txts = list(PLIEGOS_DIR.rglob("*.txt"))
    print(f"  TXT extraídos:      {len(txts)}")

    print("\n## 4. Código fuente")
    src = PROJECT_ROOT / "src"
    py_files = list(src.rglob("*.py"))
    total_lines = sum(len(f.read_text(encoding="utf-8").splitlines()) for f in py_files)
    print(f"  Ficheros .py en src/:  {len(py_files)} ({total_lines} líneas)")
    scripts = list((PROJECT_ROOT / "scripts").glob("*.py"))
    total_scripts = sum(len(f.read_text(encoding="utf-8").splitlines()) for f in scripts)
    print(f"  Scripts:                {len(scripts)} ({total_scripts} líneas)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
