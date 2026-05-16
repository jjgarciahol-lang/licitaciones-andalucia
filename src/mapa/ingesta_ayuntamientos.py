"""Ingesta de ayuntamientos: 45 municipios de Cádiz (en V1).

Estrategia:
- La lista canónica de municipios + código INE la extraemos del propio CSV
  de centros docentes de la Junta (todo municipio tiene al menos un centro,
  y los códigos INE son los oficiales). Así evitamos depender de otro dataset
  externo solo para conseguir 45 nombres.
- Para la SEDE FÍSICA de cada ayuntamiento usamos Nominatim con query
  "Ayuntamiento de {municipio}, {provincia}, España", acotada al bbox de la
  provincia para descartar homónimos (p. ej. "Conil" o "Rota" existen en otros
  países y comunidades).
- Si Nominatim falla o no devuelve un resultado dentro del bbox, persistimos
  el ayuntamiento sin coordenadas — el geocoding se puede reintentar luego.
"""
from __future__ import annotations

import csv
import hashlib
import logging
from typing import Iterable

from src.mapa.config_mapa import (
    BBOX_PROVINCIAS,
    JUNTA_CENTROS_CSV_LOCAL,
    PROVINCIAS_MAPA,
)
from src.mapa.db import conexion
from src.mapa.geocoder import Geocoder
from src.mapa.ingesta_centros import upsert_cliente
from src.mapa.modelos import ClientePotencial

log = logging.getLogger(__name__)


def extraer_municipios_desde_csv() -> list[dict[str, str]]:
    """Devuelve la lista (codigo_ine, nombre, provincia) de los municipios
    de las provincias activas, leyendo el CSV de centros de la Junta.
    """
    provincias_objetivo = {p.lower() for p in PROVINCIAS_MAPA}
    vistos: dict[str, dict[str, str]] = {}

    with JUNTA_CENTROS_CSV_LOCAL.open("r", encoding="utf-8", newline="") as f:
        for fila in csv.DictReader(f, delimiter=";"):
            provincia = (fila.get("D_PROVINCIA") or "").strip()
            if provincia.lower() not in provincias_objetivo:
                continue
            codigo_ine = (fila.get("cod_municipio") or "").strip()
            nombre = (fila.get("D_MUNICIPIO") or "").strip()
            if not codigo_ine or not nombre:
                continue
            if codigo_ine not in vistos:
                vistos[codigo_ine] = {
                    "codigo_ine": codigo_ine,
                    "nombre": nombre,
                    "provincia": provincia,
                }

    return sorted(vistos.values(), key=lambda m: m["nombre"])


def _id_estable(codigo_ine: str) -> str:
    return hashlib.sha1(f"ayuntamiento:ine:{codigo_ine}".encode("utf-8")).hexdigest()[:16]


def _coords_en_bbox(lat: float, lon: float, bbox: tuple[float, float, float, float]) -> bool:
    lon_min, lat_min, lon_max, lat_max = bbox
    return lon_min <= lon <= lon_max and lat_min <= lat <= lat_max


def _geocodificar_sede(geocoder: Geocoder, municipio: str, provincia: str) -> tuple[float | None, float | None, str | None]:
    """Devuelve (lat, lon, direccion_formateada) o (None, None, None) si no se localiza.

    Se acota al bounding box de la provincia para descartar homónimos.
    """
    bbox = BBOX_PROVINCIAS.get(provincia)
    query = f"Ayuntamiento de {municipio}, {provincia}, España"
    resultado = geocoder.buscar(query, viewbox=bbox)
    if resultado.lat is None or resultado.lon is None:
        return None, None, None
    if bbox and not _coords_en_bbox(resultado.lat, resultado.lon, bbox):
        log.warning("Resultado fuera de bbox %s para %s: %s,%s",
                    provincia, municipio, resultado.lat, resultado.lon)
        return None, None, None
    return resultado.lat, resultado.lon, resultado.display_name


def ingestar() -> dict[str, int]:
    municipios = extraer_municipios_desde_csv()
    log.info("Procesando %d municipios", len(municipios))

    nuevos = 0
    actualizados = 0
    geocodificados = 0
    sin_coordenadas = 0
    cache_hits = 0

    with Geocoder() as geocoder, conexion() as conn:
        # Pre-cache hits (para saber cuántos pegan a la red de verdad)
        cache_hits = _contar_cache_hits(geocoder, municipios)

        conn.execute("BEGIN")
        try:
            for m in municipios:
                lat, lon, direccion = _geocodificar_sede(geocoder, m["nombre"], m["provincia"])
                if lat is not None:
                    geocodificados += 1
                else:
                    sin_coordenadas += 1

                cliente = ClientePotencial(
                    id=_id_estable(m["codigo_ine"]),
                    tipo="ayuntamiento",
                    nombre=f"Ayuntamiento de {m['nombre']}",
                    direccion=direccion,
                    municipio=m["nombre"],
                    provincia=m["provincia"],
                    lat=lat,
                    lon=lon,
                    codigo_origen=m["codigo_ine"],
                    fuente="nominatim",
                    confianza="alta" if lat is not None else "baja",
                )
                resultado = upsert_cliente(conn, cliente)
                if resultado == "nuevo":
                    nuevos += 1
                else:
                    actualizados += 1
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise

    return {
        "municipios": len(municipios),
        "nuevos": nuevos,
        "actualizados": actualizados,
        "geocodificados": geocodificados,
        "sin_coordenadas": sin_coordenadas,
        "cache_hits": cache_hits,
    }


def _contar_cache_hits(geocoder: Geocoder, municipios: Iterable[dict[str, str]]) -> int:
    """Cuenta cuántas queries ya están en cache (informativo)."""
    n = 0
    for m in municipios:
        key = f"Ayuntamiento de {m['nombre']}, {m['provincia']}, España".strip().lower()
        if geocoder.conn.execute("SELECT 1 FROM geocache WHERE query = ?", (key,)).fetchone():
            n += 1
    return n
