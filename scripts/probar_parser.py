"""Ejecuta el parser CODICE sobre un ZIP descargado y muestra estadísticas.

No toca la base de datos. Sólo lectura para validar que el parser extrae
correctamente los campos.

Uso:
    python scripts/probar_parser.py 2026 5
    python scripts/probar_parser.py 2026 5 --todos-estados  # no filtrar a PUB
    python scripts/probar_parser.py 2026 5 --muestras 5     # imprimir N muestras
"""
from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config import (  # noqa: E402
    PROVINCIAS_PERMITIDAS, es_provincia_permitida, es_cpv_relevante,
)
from src.ingesta.codice_parser import parsear_zip  # noqa: E402
from src.ingesta.placsp_downloader import ruta_local  # noqa: E402
from src.logging_setup import configurar_logging  # noqa: E402


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("year", type=int)
    p.add_argument("month", type=int)
    p.add_argument("--todos-estados", action="store_true")
    p.add_argument("--muestras", type=int, default=3)
    args = p.parse_args()

    log = configurar_logging("probar_parser")
    zip_path = ruta_local(args.year, args.month)
    if not zip_path.exists():
        log.error("No existe el ZIP %s. Ejecuta primero descargar_mes.py", zip_path)
        return 1

    log.info("Parseando %s", zip_path)
    contadores: dict[str, Counter] = {
        "provincia": Counter(),
        "tipo_contrato": Counter(),
        "procedimiento": Counter(),
        "estado": Counter(),
        "cpv_principal": Counter(),
    }
    total = 0
    sin_provincia = 0
    andaluzas = 0
    andaluzas_cpv_relevante = 0
    con_docs = 0
    importes_validos = 0
    muestras_andaluzas = []
    muestras_sin_provincia = []
    muestras_cpv_relevante = []

    for licit in parsear_zip(zip_path, solo_pub=not args.todos_estados):
        total += 1
        contadores["provincia"][licit.provincia or "(sin provincia)"] += 1
        contadores["tipo_contrato"][licit.tipo_contrato or "(?)"] += 1
        contadores["procedimiento"][licit.procedimiento or "(?)"] += 1
        contadores["estado"][licit.estado or "(?)"] += 1
        contadores["cpv_principal"][licit.cpv_principal or "(sin cpv)"] += 1
        if licit.provincia is None:
            sin_provincia += 1
            if len(muestras_sin_provincia) < args.muestras:
                muestras_sin_provincia.append(licit)
        if es_provincia_permitida(licit.provincia):
            andaluzas += 1
            if len(muestras_andaluzas) < args.muestras:
                muestras_andaluzas.append(licit)
            if es_cpv_relevante(licit.cpv_principal):
                andaluzas_cpv_relevante += 1
                if len(muestras_cpv_relevante) < args.muestras:
                    muestras_cpv_relevante.append(licit)
        if licit.documentos:
            con_docs += 1
        if licit.importe_sin_iva is not None or licit.importe_con_iva is not None:
            importes_validos += 1

        if total % 5000 == 0:
            log.info("  Procesadas %d licitaciones...", total)

    print()
    print(f"{'='*70}")
    print(f"RESUMEN — {args.year}-{args.month:02d}")
    print(f"{'='*70}")
    print(f"Total licitaciones (estado={'PUB' if not args.todos_estados else 'todos'}): {total}")
    print(f"Con importe válido:            {importes_validos}")
    print(f"Con documentos adjuntos:       {con_docs}")
    print(f"Sin provincia detectable:      {sin_provincia}")
    print(f"En Andalucía:                  {andaluzas}")
    print(f"Andaluzas con CPV relevante:   {andaluzas_cpv_relevante}")
    print()

    print(f"--- Top 15 provincias ---")
    for prov, n in contadores["provincia"].most_common(15):
        marca = "*" if prov in {p.title() for p in [
            'cádiz','sevilla','málaga','granada','almería','jaén','córdoba','huelva']} else " "
        print(f"  {marca} {n:6d}  {prov}")

    print(f"\n--- Tipo de contrato ---")
    for tipo, n in contadores["tipo_contrato"].most_common():
        print(f"    {n:6d}  {tipo}")

    print(f"\n--- Procedimiento ---")
    for proc, n in contadores["procedimiento"].most_common():
        print(f"    {n:6d}  {proc}")

    print(f"\n--- Top 10 CPV principales (cualquier provincia) ---")
    for cpv, n in contadores["cpv_principal"].most_common(10):
        print(f"    {n:6d}  {cpv}")

    def imprimir_muestras(titulo: str, muestras: list) -> None:
        if not muestras:
            return
        print(f"\n--- {titulo} ---")
        for i, lic in enumerate(muestras, 1):
            print(f"\n  [{i}] {lic.expediente} — {lic.objeto[:80] if lic.objeto else '(sin objeto)'}")
            print(f"      provincia={lic.provincia}  municipio={lic.municipio}")
            print(f"      órgano={lic.organo_contratacion}")
            print(f"      cpv={lic.cpv_principal} | tipo={lic.tipo_contrato} | proc={lic.procedimiento}")
            print(f"      importe_sin_iva={lic.importe_sin_iva}  con_iva={lic.importe_con_iva}")
            print(f"      fecha_limite={lic.fecha_limite_presentacion}  estado={lic.estado}")
            print(f"      documentos={len(lic.documentos)}  enlace={lic.enlace_placsp[:80] if lic.enlace_placsp else None}")

    imprimir_muestras("Muestras andaluzas (cualquier CPV)", muestras_andaluzas)
    imprimir_muestras("Muestras andaluzas con CPV relevante", muestras_cpv_relevante)
    imprimir_muestras("Muestras sin provincia detectable", muestras_sin_provincia)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
