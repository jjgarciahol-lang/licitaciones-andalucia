"""Muestra los documentos listados de una licitación."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.db import conexion  # noqa: E402


def main() -> int:
    if len(sys.argv) < 2:
        print("Uso: python scripts/mostrar_docs.py ID")
        return 1
    lic_id = int(sys.argv[1])
    with conexion() as conn:
        fila = conn.execute(
            "SELECT expediente, objeto, documentos_urls FROM licitaciones WHERE id = ?",
            (lic_id,),
        ).fetchone()
        if fila is None:
            print("No existe")
            return 1
        print(f"Expediente: {fila['expediente']}")
        print(f"Objeto: {fila['objeto']}")
        if fila["documentos_urls"]:
            docs = json.loads(fila["documentos_urls"])
            print(f"Documentos listados: {len(docs)}")
            for d in docs:
                print(f"  - tipo={d.get('tipo')} nombre={d.get('nombre')}")
        else:
            print("Sin documentos listados")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
