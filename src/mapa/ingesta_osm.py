"""Ingesta de clientes potenciales desde OpenStreetMap vía Overpass API.

Cubre dos tipos en V1:
- `camping` (tag `tourism=camp_site`) — cobertura alta, comprador real
  para Higiofi (parques infantiles, biosaludables, pavimentos, mobiliario).
- `paisajista` (varios tags) — agrupa empresas de jardinería y obra civil
  pequeña; cobertura BAJA en OSM, pero los que están suelen estar bien
  ubicados. Útil como capa base que la comercial puede ampliar a mano.

Estrategia de identificación de "paisajistas":
- `shop=garden_centre` (centros de jardinería / viveros)
- `craft=gardener` (paisajistas profesionales)
- `office=construction_company` (constructoras)
- `craft=builder` (albañiles / pequeña construcción)

Esto traerá ruido (algunas grandes constructoras no aplicables), pero los
ayuntamientos ya filtran tamaño al exigir clasificación administrativa
en sus licitaciones. Para el canal "puerta a puerta" comercial es base
suficiente.
"""
from __future__ import annotations

import hashlib
import logging

from src.mapa.config_mapa import BBOX_PROVINCIAS, PROVINCIAS_MAPA
from src.mapa.db import conexion
from src.mapa.ingesta_centros import upsert_cliente
from src.mapa.modelos import ClientePotencial
from src.mapa.overpass import ElementoOSM, construir_query_provincia, consultar

log = logging.getLogger(__name__)


# ============================== CAMPINGS =====================================

FILTROS_CAMPING = ['["tourism"="camp_site"]']


def _id_estable_osm(elemento: ElementoOSM) -> str:
    """ID estable basado en (osm_type, osm_id) — sobrevive a reejecuciones."""
    clave = f"osm:{elemento.osm_type}:{elemento.osm_id}"
    return hashlib.sha1(clave.encode("utf-8")).hexdigest()[:16]


def _nombre_o_fallback(tags: dict[str, str], fallback: str) -> str:
    """Devuelve el name si existe, si no genera uno descriptivo."""
    return tags.get("name") or tags.get("operator") or f"{fallback} (sin nombre OSM)"


def _direccion_desde_tags(tags: dict[str, str]) -> str | None:
    partes = []
    if tags.get("addr:street"):
        calle = tags["addr:street"]
        if tags.get("addr:housenumber"):
            calle = f"{calle}, {tags['addr:housenumber']}"
        partes.append(calle)
    if tags.get("addr:postcode"):
        partes.append(tags["addr:postcode"])
    if tags.get("addr:city"):
        partes.append(tags["addr:city"])
    return ", ".join(partes) if partes else None


def _coords_en_bbox(lat: float, lon: float, bbox) -> bool:
    lon_min, lat_min, lon_max, lat_max = bbox
    return lon_min <= lon <= lon_max and lat_min <= lat <= lat_max


def _elemento_a_cliente(
    elemento: ElementoOSM,
    tipo: str,
    provincia: str,
    nombre_fallback: str,
) -> ClientePotencial | None:
    if elemento.lat is None or elemento.lon is None:
        return None
    # Defensa extra contra elementos que caen fuera del bbox provincial
    # (Overpass a veces incluye marginales por la zona buffer del área).
    bbox = BBOX_PROVINCIAS.get(provincia)
    if bbox and not _coords_en_bbox(elemento.lat, elemento.lon, bbox):
        return None
    nombre = _nombre_o_fallback(elemento.tags, nombre_fallback)
    return ClientePotencial(
        id=_id_estable_osm(elemento),
        tipo=tipo,
        nombre=nombre,
        direccion=_direccion_desde_tags(elemento.tags),
        municipio=elemento.tags.get("addr:city"),
        provincia=provincia,
        cp=elemento.tags.get("addr:postcode"),
        telefono=elemento.tags.get("phone") or elemento.tags.get("contact:phone"),
        email=elemento.tags.get("email") or elemento.tags.get("contact:email"),
        web=elemento.tags.get("website") or elemento.tags.get("contact:website"),
        lat=elemento.lat,
        lon=elemento.lon,
        codigo_origen=f"{elemento.osm_type}/{elemento.osm_id}",
        fuente="osm",
        confianza="alta" if elemento.tags.get("name") else "media",
    )


def ingestar_campings() -> dict[str, int]:
    return _ingestar_por_tipo(
        filtros=FILTROS_CAMPING,
        tipo="camping",
        nombre_fallback="Camping",
    )


# NOTA: la categoría "competencia" se llena ahora desde PLACSP, no desde OSM
# (ver src/mapa/ingesta_contratistas.py:ingestar_competencia_higiofi). Los
# viveros/jardinerías que metíamos por OSM NO son competencia real de Higiofi
# — venden plantas, no mobiliario urbano ni parques infantiles.


# ============================== ORQUESTACIÓN =================================

def _ingestar_por_tipo(filtros: list[str], tipo: str, nombre_fallback: str) -> dict[str, int]:
    total_leidos = 0
    sin_coords = 0
    fuera_bbox = 0
    nuevos = 0
    actualizados = 0

    for provincia in PROVINCIAS_MAPA:
        log.info("Consultando Overpass para %s en %s", tipo, provincia)
        query = construir_query_provincia(provincia, filtros)
        elementos = consultar(query)
        log.info("  %d elementos recibidos", len(elementos))
        total_leidos += len(elementos)

        # Dedupe por (osm_type, osm_id): una entidad puede aparecer en varias
        # consultas si tiene múltiples tags relevantes.
        vistos: set[tuple[str, int]] = set()

        with conexion() as conn:
            conn.execute("BEGIN")
            try:
                for elemento in elementos:
                    clave = (elemento.osm_type, elemento.osm_id)
                    if clave in vistos:
                        continue
                    vistos.add(clave)

                    if elemento.lat is None or elemento.lon is None:
                        sin_coords += 1
                        continue

                    cliente = _elemento_a_cliente(elemento, tipo, provincia, nombre_fallback)
                    if cliente is None:
                        fuera_bbox += 1
                        continue

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
        "leidos": total_leidos,
        "sin_coords": sin_coords,
        "fuera_bbox": fuera_bbox,
        "nuevos": nuevos,
        "actualizados": actualizados,
    }
