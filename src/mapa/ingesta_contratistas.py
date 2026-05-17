"""Ingesta de contratistas locales: empresas que han ganado obra pública.

Cruza la tabla `adjudicaciones` (licitaciones.db, poblada por
`scripts/extraer_adjudicaciones.py`) con el mapa comercial:

- Selecciona empresas con sede en las provincias activas del mapa.
- Agrupa por NIF: cuenta adjudicaciones, suma importes, queda con la última
  razón social.
- Geocodifica el municipio de la empresa (no la calle — el dataset PLACSP
  raramente trae calle, y para visualizar comercial basta con la ciudad).
- Aplica offset determinista por hash(NIF) para que varias empresas del mismo
  municipio no queden exactamente en la misma coordenada.
- Persiste como `tipo='contratista_local'` en `clientes.db`.
"""
from __future__ import annotations

import hashlib
import logging
import sqlite3
from math import cos, radians

from src.config import DB_PATH as DB_LICITACIONES
from src.mapa.config_mapa import BBOX_PROVINCIAS, PROVINCIAS_MAPA
from src.mapa.db import conexion as conexion_mapa
from src.mapa.geocoder import Geocoder
from src.mapa.ingesta_centros import upsert_cliente
from src.mapa.modelos import ClientePotencial

log = logging.getLogger(__name__)


def _id_estable_nif(nif: str) -> str:
    """ID estable: SHA1 de 'contratista:nif:{NIF}'."""
    return hashlib.sha1(f"contratista:nif:{nif}".encode("utf-8")).hexdigest()[:16]


def _offset_por_nif(nif: str) -> tuple[float, float]:
    """Devuelve un offset (dlat, dlon) determinista para un NIF.

    Sirve para que varias empresas del mismo municipio (que comparten centroide)
    no aparezcan exactamente en la misma coordenada. Genera puntos en un radio
    de ~150m alrededor del centroide.
    """
    h = hashlib.md5(nif.encode("utf-8")).digest()
    # Usamos dos bytes como ángulo y distancia
    angulo_deg = (h[0] / 255.0) * 360.0
    distancia_m = 50 + (h[1] / 255.0) * 150.0  # entre 50 y 200 metros

    import math
    angulo_rad = math.radians(angulo_deg)
    # 1 grado lat ≈ 111000 m; 1 grado lon ≈ 111000 * cos(lat) ≈ 90000 en Cádiz
    dlat = (distancia_m * math.cos(angulo_rad)) / 111000.0
    dlon = (distancia_m * math.sin(angulo_rad)) / 90000.0
    return dlat, dlon


def _coords_en_bbox(lat: float, lon: float, bbox: tuple[float, float, float, float]) -> bool:
    lon_min, lat_min, lon_max, lat_max = bbox
    return lon_min <= lon <= lon_max and lat_min <= lat <= lat_max


def _formato_importe(eur: float | None) -> str:
    if not eur or eur <= 0:
        return ""
    if eur >= 1_000_000:
        return f"€{eur / 1_000_000:.1f}M"
    return f"€{eur / 1_000:.0f}k"


def _formato_descripcion(municipio: str | None, n_adj: int, importe_total: float | None) -> str:
    """Texto que va en el campo `direccion` del ClientePotencial."""
    partes = [municipio or "Cádiz"]
    forma = "adjudicación pública" if n_adj == 1 else "adjudicaciones públicas"
    partes.append(f"{n_adj} {forma}")
    imp = _formato_importe(importe_total)
    if imp:
        partes.append(f"{imp} total adjudicado")
    return " · ".join(partes)


def _consultar_contratistas() -> list[dict]:
    """Lee contratistas agregados desde licitaciones.db."""
    provincias = list(PROVINCIAS_MAPA)
    placeholders = ",".join("?" * len(provincias))
    conn = sqlite3.connect(str(DB_LICITACIONES))
    conn.row_factory = sqlite3.Row
    try:
        q = f"""
            SELECT
                nif,
                MAX(razon_social) AS razon_social,
                MAX(ciudad)       AS ciudad,
                provincia,
                COUNT(*)          AS n_adjudicaciones,
                SUM(importe_adjudicacion) AS importe_total,
                MAX(fecha_adjudicacion)   AS ultima_fecha
            FROM adjudicaciones
            WHERE nif IS NOT NULL
              AND razon_social IS NOT NULL
              AND provincia IN ({placeholders})
            GROUP BY nif
            ORDER BY n_adjudicaciones DESC, importe_total DESC
        """
        return [dict(r) for r in conn.execute(q, provincias)]
    finally:
        conn.close()


def _geocodificar_municipio(geocoder: Geocoder, ciudad: str, provincia: str) -> tuple[float | None, float | None]:
    """Geocodifica solo el municipio (centroide). Cacheado."""
    query = f"{ciudad}, {provincia}, España"
    bbox = BBOX_PROVINCIAS.get(provincia)
    resultado = geocoder.buscar(query, viewbox=bbox)
    if resultado.lat is None or resultado.lon is None:
        return None, None
    if bbox and not _coords_en_bbox(resultado.lat, resultado.lon, bbox):
        log.warning("Resultado fuera de bbox %s para municipio %r", provincia, ciudad)
        return None, None
    return resultado.lat, resultado.lon


def ingestar() -> dict[str, int]:
    contratistas = _consultar_contratistas()
    log.info("Contratistas a procesar: %d", len(contratistas))

    nuevos = 0
    actualizados = 0
    sin_ciudad = 0
    sin_coords = 0
    geocoded = 0

    # Cache de geocoding por municipio
    cache_municipios: dict[tuple[str, str], tuple[float | None, float | None]] = {}

    with Geocoder() as geocoder, conexion_mapa() as conn:
        conn.execute("BEGIN")
        try:
            for c in contratistas:
                ciudad = (c["ciudad"] or "").strip()
                if not ciudad:
                    sin_ciudad += 1
                    continue

                # Normalizo capitalización para mejor cache hit
                ciudad_clave = ciudad.strip().lower()
                clave = (ciudad_clave, c["provincia"])
                if clave not in cache_municipios:
                    cache_municipios[clave] = _geocodificar_municipio(
                        geocoder, ciudad, c["provincia"]
                    )
                lat_base, lon_base = cache_municipios[clave]
                if lat_base is None or lon_base is None:
                    sin_coords += 1
                    continue
                geocoded += 1

                dlat, dlon = _offset_por_nif(c["nif"])
                lat = lat_base + dlat
                lon = lon_base + dlon

                cliente = ClientePotencial(
                    id=_id_estable_nif(c["nif"]),
                    tipo="contratista_local",
                    nombre=c["razon_social"],
                    direccion=_formato_descripcion(
                        ciudad, c["n_adjudicaciones"], c["importe_total"]
                    ),
                    municipio=ciudad.title() if ciudad.isupper() or ciudad.islower() else ciudad,
                    provincia=c["provincia"],
                    lat=lat,
                    lon=lon,
                    codigo_origen=c["nif"],
                    fuente="placsp_adjudicaciones",  # derivado de licitaciones.db
                    confianza="media",  # coords son centroide municipal + offset
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
        "contratistas_leidos": len(contratistas),
        "sin_ciudad": sin_ciudad,
        "sin_coords": sin_coords,
        "geocoded": geocoded,
        "nuevos": nuevos,
        "actualizados": actualizados,
        "municipios_unicos": len(cache_municipios),
    }
