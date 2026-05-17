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
import math
import sqlite3

from src.config import CPVS_RELEVANTES, DB_PATH as DB_LICITACIONES
from src.mapa.config_mapa import (
    BBOX_PROVINCIAS, CPVS_MOBILIARIO, CPVS_PARQUES, PROVINCIAS_MAPA,
    cargar_blacklist_competencia,
)


def _clasificar_categoria(cpvs_ganados: str | None) -> str:
    """Devuelve 'competencia_parques' o 'competencia_mobiliario' según los CPVs
    ganados por la empresa. Si gana en AMBOS, prevalece 'parques' (categoría
    más especializada de Higiofi).
    """
    if not cpvs_ganados:
        return "competencia_mobiliario"  # fallback conservador
    cpvs = [c.strip() for c in cpvs_ganados.split(",")]
    for cpv in cpvs:
        if any(cpv.startswith(p) for p in CPVS_PARQUES):
            return "competencia_parques"
    return "competencia_mobiliario"
from src.mapa.db import conexion as conexion_mapa
from src.mapa.geocoder import Geocoder
from src.mapa.ingesta_centros import upsert_cliente
from src.mapa.modelos import ClientePotencial

# Provincias andaluzas — para filtrar "licitaciones en Andalucía" cuando se
# busca competencia real (empresas que ganaron CPVs Higiofi en territorio de
# la comercial).
PROVINCIAS_ANDALUCIA = (
    "Cádiz", "Sevilla", "Málaga", "Granada", "Huelva", "Jaén", "Córdoba", "Almería",
)

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
    angulo_deg = (h[0] / 255.0) * 360.0
    distancia_m = 50 + (h[1] / 255.0) * 150.0  # entre 50 y 200 metros
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


# ============================== COMPETENCIA HIGIOFI ==========================
# Empresas que han ganado licitaciones con CPVs del catálogo Higiofi
# (mobiliario urbano, parques infantiles, mobiliario escolar, papelería) en
# Andalucía. Son competencia REAL — no como los viveros de OSM que vendían
# plantas. Aquí Kompan, Benito Urban, HPC Ibérica, etc. aparecerán.

def _formato_descripcion_competencia(municipio: str | None, n_adj: int, importe: float | None, cpvs: str | None) -> str:
    partes = [municipio or "?"]
    forma = "victoria en Andalucía" if n_adj == 1 else "victorias en Andalucía"
    partes.append(f"{n_adj} {forma}")
    imp = _formato_importe(importe)
    if imp:
        partes.append(f"{imp} total")
    if cpvs:
        # Tomamos solo el primer CPV como muestra, formateado
        primer_cpv = cpvs.split(",")[0].strip()
        partes.append(f"CPV {primer_cpv}")
    return " · ".join(partes)


# Palabras clave en razón social que indican "constructora / obra civil" más que
# "proveedor de mobiliario". Si aparecen en el nombre, la empresa probablemente
# COMPRA productos Higiofi para instalarlos (cliente) en lugar de venderlos
# (competencia). No es infalible — NUPREN, por ejemplo, no contiene ninguna de
# estas — pero filtra los casos más obvios.
_KEYWORDS_CONSTRUCTORA = (
    "CONSTRUC", "OBRA", "EDIFIC", "INGENIER",
    "RESTAURACION", "REFORMAS", "PROMOCIONES",
)


def _consultar_competencia_higiofi() -> list[dict]:
    """Empresas que han ganado licitaciones con CPV del catálogo Higiofi en
    Andalucía como SUMINISTRO (no como obra). Excluye empresas cuya razón
    social sugiere que son constructoras (que comprarían a Higiofi, no
    compiten con él).
    """
    cpv_or = " OR ".join([f"l.cpv_principal LIKE '{p}%'" for p in CPVS_RELEVANTES])
    placeholders = ",".join("?" * len(PROVINCIAS_ANDALUCIA))
    # Filtro adicional contra nombres de constructora
    name_excl = " AND ".join([f"UPPER(a.razon_social) NOT LIKE '%{kw}%'" for kw in _KEYWORDS_CONSTRUCTORA])
    conn = sqlite3.connect(str(DB_LICITACIONES))
    conn.row_factory = sqlite3.Row
    try:
        q = f"""
            SELECT
                a.nif,
                MAX(a.razon_social) AS razon_social,
                MAX(a.ciudad)       AS ciudad,
                MAX(a.provincia)    AS provincia,
                COUNT(*)            AS n_adjudicaciones,
                SUM(a.importe_adjudicacion) AS importe_total,
                GROUP_CONCAT(DISTINCT l.cpv_principal) AS cpvs_ganados
            FROM adjudicaciones a
            JOIN licitaciones l ON a.uuid_placsp = l.uuid_placsp
            WHERE ({cpv_or})
              AND l.tipo_contrato = 'Suministros'
              AND l.provincia IN ({placeholders})
              AND a.nif IS NOT NULL
              AND a.razon_social IS NOT NULL
              AND {name_excl}
            GROUP BY a.nif
            ORDER BY n_adjudicaciones DESC, importe_total DESC
        """
        return [dict(r) for r in conn.execute(q, PROVINCIAS_ANDALUCIA)]
    finally:
        conn.close()


def _id_estable_competidor(nif: str) -> str:
    return hashlib.sha1(f"competidor:nif:{nif}".encode("utf-8")).hexdigest()[:16]


def ingestar_competencia_higiofi() -> dict[str, int]:
    """Pobla `competencia` en clientes.db con empresas que han ganado
    licitaciones de CPVs del catálogo Higiofi en Andalucía."""
    empresas = _consultar_competencia_higiofi()
    blacklist = cargar_blacklist_competencia()
    if blacklist:
        antes = len(empresas)
        empresas = [e for e in empresas if e["nif"] not in blacklist]
        log.info("Blacklist aplicada: %d NIFs excluidos (de %d → %d candidatos)",
                 antes - len(empresas), antes, len(empresas))
    log.info("Competidores a procesar: %d", len(empresas))

    nuevos = 0
    actualizados = 0
    sin_ciudad = 0
    sin_coords = 0

    # Cache de geocoding: igual que en contratistas, geocodificamos solo el
    # municipio. Aquí las empresas pueden estar en cualquier provincia de
    # España, no solo Andalucía — la competencia es nacional.
    cache_municipios: dict[tuple[str, str], tuple[float | None, float | None]] = {}

    with Geocoder() as geocoder, conexion_mapa() as conn:
        conn.execute("BEGIN")
        try:
            for c in empresas:
                ciudad = (c["ciudad"] or "").strip()
                provincia = (c["provincia"] or "").strip()
                if not ciudad:
                    sin_ciudad += 1
                    continue

                # Si la "provincia" parece un código NUTS (ej. ES424), no lo
                # incluimos en la query a Nominatim — solo confunde. Para esos
                # casos vamos con "ciudad, España".
                es_nuts = (
                    provincia and provincia.startswith("ES")
                    and len(provincia) == 5
                )
                provincia_query = "España" if (not provincia or es_nuts) else provincia
                clave = (ciudad.lower(), provincia_query.lower())
                if clave not in cache_municipios:
                    query = f"{ciudad}, {provincia_query}"
                    resultado = geocoder.buscar(query)
                    cache_municipios[clave] = (resultado.lat, resultado.lon)

                lat_base, lon_base = cache_municipios[clave]
                if lat_base is None or lon_base is None:
                    sin_coords += 1
                    continue

                dlat, dlon = _offset_por_nif(c["nif"])
                lat = lat_base + dlat
                lon = lon_base + dlon

                categoria = _clasificar_categoria(c["cpvs_ganados"])
                cliente = ClientePotencial(
                    id=_id_estable_competidor(c["nif"]),
                    tipo=categoria,
                    nombre=c["razon_social"],
                    direccion=_formato_descripcion_competencia(
                        ciudad, c["n_adjudicaciones"],
                        c["importe_total"], c["cpvs_ganados"],
                    ),
                    municipio=ciudad.title() if ciudad.isupper() or ciudad.islower() else ciudad,
                    # provincia es NOT NULL en el esquema. Si no la conocemos
                    # (NUTS no mapeado, etc.) ponemos un fallback descriptivo.
                    provincia=provincia if provincia and not es_nuts else "(otra)",
                    lat=lat,
                    lon=lon,
                    codigo_origen=c["nif"],
                    fuente="placsp_adjudicaciones",
                    confianza="alta",
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
        "empresas_leidas": len(empresas),
        "sin_ciudad": sin_ciudad,
        "sin_coords": sin_coords,
        "nuevos": nuevos,
        "actualizados": actualizados,
        "municipios_unicos": len(cache_municipios),
    }
