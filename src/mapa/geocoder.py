"""Geocoder con cache en SQLite usando Nominatim (OpenStreetMap).

Reglas de uso de Nominatim (Usage Policy):
- Máximo 1 request por segundo.
- User-Agent identificable (no genérico tipo "Python-requests").
- Resultados pueden cachearse indefinidamente.

Este módulo respeta el rate limit con un sleep entre requests y cachea cada
query (clave canónica) en `data/mapa/geocache.db`. Reejecutar las ingestas
no genera tráfico adicional contra Nominatim.
"""
from __future__ import annotations

import json
import logging
import sqlite3
import time
from dataclasses import dataclass

import requests

from src.mapa.config_mapa import (
    GEOCACHE_DB_PATH,
    NOMINATIM_RATE_LIMIT_S,
    NOMINATIM_USER_AGENT,
)

log = logging.getLogger(__name__)


NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"

DDL_CACHE = """
PRAGMA journal_mode = WAL;
CREATE TABLE IF NOT EXISTS geocache (
    query        TEXT PRIMARY KEY,
    lat          REAL,
    lon          REAL,
    display_name TEXT,
    osm_type     TEXT,
    osm_id       TEXT,
    raw_json     TEXT,
    exito        INTEGER NOT NULL DEFAULT 0,
    fecha        TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


@dataclass
class GeoResultado:
    lat: float | None
    lon: float | None
    display_name: str | None
    fuente_cache: bool


class Geocoder:
    """Cliente Nominatim con cache local. Pensado para usarse como context manager."""

    def __init__(self) -> None:
        self.conn: sqlite3.Connection = sqlite3.connect(
            str(GEOCACHE_DB_PATH), timeout=30.0, isolation_level=None
        )
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(DDL_CACHE)
        self._ultima_request_ts: float = 0.0

    def __enter__(self) -> "Geocoder":
        return self

    def __exit__(self, *args) -> None:
        self.conn.close()

    def buscar(self, query: str, viewbox: tuple[float, float, float, float] | None = None) -> GeoResultado:
        """Geocodifica una query.

        `viewbox` opcional: (lon_min, lat_min, lon_max, lat_max) para acotar el
        resultado a un rectángulo geográfico (mejora la precisión).
        """
        key = query.strip().lower()
        cached = self.conn.execute(
            "SELECT lat, lon, display_name, exito FROM geocache WHERE query = ?", (key,)
        ).fetchone()
        if cached:
            return GeoResultado(
                lat=cached["lat"],
                lon=cached["lon"],
                display_name=cached["display_name"],
                fuente_cache=True,
            )

        # Respetar rate limit 1 req/s
        elapsed = time.monotonic() - self._ultima_request_ts
        if elapsed < NOMINATIM_RATE_LIMIT_S:
            time.sleep(NOMINATIM_RATE_LIMIT_S - elapsed)

        params: dict[str, str] = {
            "q": query,
            "format": "json",
            "limit": "1",
            "addressdetails": "0",
            "countrycodes": "es",
        }
        if viewbox is not None:
            params["viewbox"] = ",".join(str(v) for v in viewbox)
            params["bounded"] = "1"

        try:
            resp = requests.get(
                NOMINATIM_URL,
                params=params,
                headers={"User-Agent": NOMINATIM_USER_AGENT},
                timeout=15,
            )
            self._ultima_request_ts = time.monotonic()
            resp.raise_for_status()
            datos = resp.json()
        except Exception as e:
            log.warning("Nominatim falló para %r: %s", query, e)
            self._guardar(key, None, None, None, None, None, exito=False, raw=None)
            return GeoResultado(None, None, None, fuente_cache=False)

        if not datos:
            log.info("Nominatim sin resultados para %r", query)
            self._guardar(key, None, None, None, None, None, exito=False, raw=json.dumps([]))
            return GeoResultado(None, None, None, fuente_cache=False)

        d = datos[0]
        lat = float(d["lat"])
        lon = float(d["lon"])
        display_name = d.get("display_name")
        osm_type = d.get("osm_type")
        osm_id = str(d.get("osm_id")) if "osm_id" in d else None
        self._guardar(key, lat, lon, display_name, osm_type, osm_id, exito=True, raw=json.dumps(d))
        return GeoResultado(lat=lat, lon=lon, display_name=display_name, fuente_cache=False)

    def _guardar(
        self,
        key: str,
        lat: float | None,
        lon: float | None,
        display_name: str | None,
        osm_type: str | None,
        osm_id: str | None,
        exito: bool,
        raw: str | None,
    ) -> None:
        self.conn.execute(
            """
            INSERT INTO geocache (query, lat, lon, display_name, osm_type, osm_id, raw_json, exito)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(query) DO UPDATE SET
                lat=excluded.lat, lon=excluded.lon,
                display_name=excluded.display_name,
                osm_type=excluded.osm_type, osm_id=excluded.osm_id,
                raw_json=excluded.raw_json, exito=excluded.exito,
                fecha=datetime('now')
            """,
            (key, lat, lon, display_name, osm_type, osm_id, raw, 1 if exito else 0),
        )
