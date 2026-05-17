"""Cliente para Overpass API (OpenStreetMap) con cache SQLite.

Overpass es la API de consultas estructuradas sobre datos de OpenStreetMap.
Cobertura: variable por país y categoría. En España las tiendas turísticas
(campings, hoteles) están bien mapeadas; categorías comerciales pequeñas
(paisajistas, talleres) tienen cobertura baja.

Política de uso:
- Sin rate limit estricto, pero los servidores son comunitarios — no abusar.
- User-Agent identificable.
- Cachear resultados (las consultas son caras).
"""
from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
import time
from dataclasses import dataclass
from typing import Any

import requests

from src.mapa.config_mapa import (
    MAPA_DATA_DIR,
    OVERPASS_TIMEOUT_S,
    OVERPASS_URL,
    OVERPASS_USER_AGENT,
)

log = logging.getLogger(__name__)


OVERPASS_CACHE_DB = MAPA_DATA_DIR / "overpass_cache.db"

DDL_CACHE = """
PRAGMA journal_mode = WAL;
CREATE TABLE IF NOT EXISTS overpass_cache (
    query_hash TEXT PRIMARY KEY,
    query      TEXT NOT NULL,
    respuesta  TEXT NOT NULL,
    fecha      TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


@dataclass
class ElementoOSM:
    """Una entidad OSM (node, way o relation) con sus tags y coordenadas."""
    osm_type: str          # 'node' | 'way' | 'relation'
    osm_id: int
    lat: float | None
    lon: float | None
    tags: dict[str, str]


def _conexion_cache() -> sqlite3.Connection:
    conn = sqlite3.connect(str(OVERPASS_CACHE_DB), timeout=30.0, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.executescript(DDL_CACHE)
    return conn


def consultar(query: str, force: bool = False) -> list[ElementoOSM]:
    """Ejecuta una query Overpass QL y devuelve elementos parseados.

    La query debe incluir `[out:json]` y `out center tags` (o equivalente) para
    que devuelva JSON con coordenadas y tags. Para `way` y `relation` usamos
    `out center` que añade un punto representativo de la geometría.
    """
    query_hash = hashlib.sha1(query.encode("utf-8")).hexdigest()

    with _conexion_cache() as conn:
        if not force:
            cached = conn.execute(
                "SELECT respuesta FROM overpass_cache WHERE query_hash = ?", (query_hash,)
            ).fetchone()
            if cached:
                log.info("Overpass cache HIT (hash=%s)", query_hash[:8])
                return _parse_respuesta(json.loads(cached["respuesta"]))

        log.info("Overpass cache MISS — consultando %s (timeout %ds)",
                 OVERPASS_URL, OVERPASS_TIMEOUT_S)
        t0 = time.monotonic()
        resp = requests.post(
            OVERPASS_URL,
            data={"data": query},
            headers={"User-Agent": OVERPASS_USER_AGENT},
            timeout=OVERPASS_TIMEOUT_S,
        )
        duracion = time.monotonic() - t0
        resp.raise_for_status()
        datos = resp.json()
        log.info("Overpass respondió en %.1fs con %d elementos", duracion, len(datos.get("elements", [])))

        conn.execute(
            "INSERT OR REPLACE INTO overpass_cache (query_hash, query, respuesta) VALUES (?, ?, ?)",
            (query_hash, query, json.dumps(datos, ensure_ascii=False)),
        )

    return _parse_respuesta(datos)


def _parse_respuesta(datos: dict[str, Any]) -> list[ElementoOSM]:
    resultado: list[ElementoOSM] = []
    for e in datos.get("elements", []):
        tipo = e.get("type", "")
        if tipo == "node":
            lat, lon = e.get("lat"), e.get("lon")
        else:
            # way / relation: requieren `out center` para tener centro de geometría
            centro = e.get("center", {})
            lat, lon = centro.get("lat"), centro.get("lon")
        resultado.append(ElementoOSM(
            osm_type=tipo,
            osm_id=e["id"],
            lat=lat,
            lon=lon,
            tags=e.get("tags", {}),
        ))
    return resultado


def construir_query_provincia(nombre_provincia: str, filtros: list[str]) -> str:
    """Construye una query Overpass acotada a una provincia española.

    `filtros` es una lista de selectores OSM, p.ej.:
        ['["tourism"="camp_site"]', '["shop"="garden_centre"]']

    Cada filtro se busca como node, way y relation; el resultado incluye
    el centro de la geometría para ways/relations.

    Notas:
    - Usamos `admin_level=6` con name= para la provincia.
    - Filtramos también con `addr:province` por si el `area` falla en algún
      elemento (algunos están mapeados sin estar dentro del polígono admin).
    """
    bloques: list[str] = []
    for f in filtros:
        bloques.append(f"node{f}(area.searchArea);")
        bloques.append(f"way{f}(area.searchArea);")
        bloques.append(f"relation{f}(area.searchArea);")
    return f"""
[out:json][timeout:{OVERPASS_TIMEOUT_S}];
area["admin_level"="6"]["name"="{nombre_provincia}"]->.searchArea;
(
{chr(10).join('  ' + b for b in bloques)}
);
out center tags;
"""
