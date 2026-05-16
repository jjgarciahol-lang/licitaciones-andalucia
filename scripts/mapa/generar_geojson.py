"""Exporta la tabla `clientes` a `docs/mapa/data/clientes.geojson`.

El GeoJSON resultante es lo único que consume el frontend Leaflet — la DB
SQLite se queda como staging local, no se publica.

Uso:
    python scripts/mapa/generar_geojson.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from src.logging_setup import configurar_logging  # noqa: E402
from src.mapa.config_mapa import GEOJSON_PATH  # noqa: E402
from src.mapa.db import conexion  # noqa: E402


CAMPOS_PROPERTIES = (
    "id", "tipo", "nombre", "direccion", "municipio", "provincia", "cp",
    "telefono", "email", "web", "etapas", "fuente", "confianza",
)


def main() -> int:
    log = configurar_logging("mapa_geojson")
    features: list[dict] = []
    descartados_sin_coords = 0

    with conexion() as conn:
        for fila in conn.execute(
            f"SELECT {', '.join(CAMPOS_PROPERTIES)}, lat, lon FROM clientes"
        ):
            if fila["lat"] is None or fila["lon"] is None:
                descartados_sin_coords += 1
                continue
            features.append({
                "type": "Feature",
                "geometry": {
                    "type": "Point",
                    "coordinates": [fila["lon"], fila["lat"]],
                },
                "properties": {k: fila[k] for k in CAMPOS_PROPERTIES},
            })

    geojson = {"type": "FeatureCollection", "features": features}
    GEOJSON_PATH.write_text(
        json.dumps(geojson, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )

    tamano_kb = GEOJSON_PATH.stat().st_size / 1024
    log.info("GeoJSON escrito: %s", GEOJSON_PATH)
    log.info("  features ............... %d", len(features))
    log.info("  descartados sin coords . %d", descartados_sin_coords)
    log.info("  tamaño ................. %.1f KB", tamano_kb)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
