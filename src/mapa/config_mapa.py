"""Configuración específica del subproyecto 'mapa'.

Se monta encima de src/config.py (que ya gestiona .env y rutas globales) y solo
añade las constantes propias del mapa de clientes potenciales.
"""
from __future__ import annotations

from pathlib import Path

from src.config import PROJECT_ROOT

# --- Rutas del subproyecto ----------------------------------------------------
MAPA_DATA_DIR: Path = (PROJECT_ROOT / "data" / "mapa").resolve()
CLIENTES_DB_PATH: Path = MAPA_DATA_DIR / "clientes.db"
GEOCACHE_DB_PATH: Path = MAPA_DATA_DIR / "geocache.db"
GEOJSON_PATH: Path = (PROJECT_ROOT / "docs" / "mapa" / "data" / "clientes.geojson").resolve()

MAPA_DATA_DIR.mkdir(parents=True, exist_ok=True)
GEOJSON_PATH.parent.mkdir(parents=True, exist_ok=True)

# --- Alcance geográfico V1 ----------------------------------------------------
# Empezamos solo con Cádiz para validar extremo a extremo (ingesta, geocoding,
# UX). Cuando esté pulido, se amplía añadiendo provincias a esta lista y se
# reejecutan los scripts de ingesta — el esquema y el frontend no cambian.
PROVINCIAS_MAPA: tuple[str, ...] = ("Cádiz",)

# Lista completa preparada para V2 (todas las provincias andaluzas sin Almería,
# que está excluida por decisión comercial — mismo criterio que licitaciones).
PROVINCIAS_ANDALUCIA_SIN_ALMERIA: tuple[str, ...] = (
    "Cádiz", "Sevilla", "Málaga", "Granada", "Jaén", "Córdoba", "Huelva",
)

# --- Tipos de cliente ---------------------------------------------------------
TIPOS_CLIENTE: tuple[str, ...] = (
    "colegio_publico",
    "colegio_concertado",  # no se puebla en V1 (la fuente Junta no lo distingue)
    "colegio_privado",
    "guarderia",
    "ayuntamiento",
    "camping",             # via OSM/Overpass
    "paisajista",          # via OSM/Overpass — agrupa jardinería + obra civil pequeña
)

# Tipos ocultos por defecto en el frontend (vacío en V1).
TIPOS_OCULTOS_POR_DEFECTO: tuple[str, ...] = ()

# --- Fuentes de datos ---------------------------------------------------------
FUENTES: tuple[str, ...] = (
    "junta_andalucia",   # Catálogo de centros docentes
    "ine",               # Municipios y centroides
    "mptfp",             # Registro de Entidades Locales (contactos)
    "osm",               # Overpass API (campings, paisajistas, obra civil)
    "nominatim",         # Geocoding (sede ayuntamientos)
    "manual",            # Correcciones a mano
)

# --- Overpass API -------------------------------------------------------------
OVERPASS_URL = "https://overpass-api.de/api/interpreter"
OVERPASS_USER_AGENT = "HigiofiMapaComercial/1.0 (contacto: higiofi.es)"
OVERPASS_TIMEOUT_S = 90

# --- Nominatim ----------------------------------------------------------------
# Política de uso de OSM exige User-Agent identificable y rate limit 1 req/s.
NOMINATIM_USER_AGENT = "HigiofiMapaComercial/1.0 (contacto: higiofi.es)"
NOMINATIM_RATE_LIMIT_S = 1.1  # margen de seguridad sobre el 1.0 oficial

# --- Junta de Andalucía: Directorio de Centros Docentes -----------------------
# Dataset oficial, actualizado anualmente con el curso académico vigente.
# URL del dataset (página intermedia): https://www.juntadeandalucia.es/datosabiertos/portal/dataset/directorio-de-centros-docentes-de-andalucia
JUNTA_CENTROS_CSV_URL = (
    "https://www.juntadeandalucia.es/datosabiertos/portal/dataset/"
    "e039df22-4b82-4d0d-9884-0ab5952e24e4/resource/"
    "b5924e81-0b53-4418-9d93-b1f39ba1ef65/download/da_centros.csv"
)
JUNTA_CENTROS_CSV_LOCAL: Path = MAPA_DATA_DIR / "raw" / "da_centros.csv"
JUNTA_CENTROS_CSV_LOCAL.parent.mkdir(parents=True, exist_ok=True)

# --- Bounding boxes por provincia (lon_min, lat_min, lon_max, lat_max) -------
# Usados para acotar resultados de Nominatim a la provincia correcta y para
# validar a posteriori que las coordenadas obtenidas son plausibles.
BBOX_PROVINCIAS: dict[str, tuple[float, float, float, float]] = {
    "Cádiz":   (-6.70, 35.95, -5.05, 37.10),
    "Huelva":  (-7.55, 36.85, -6.30, 38.10),
    "Sevilla": (-6.60, 36.90, -4.65, 38.10),
    "Málaga":  (-5.65, 36.30, -3.85, 37.30),
    "Granada": (-4.70, 36.70, -2.30, 38.20),
    "Jaén":    (-4.30, 37.40, -2.20, 38.75),
    "Córdoba": (-5.65, 37.40, -3.95, 38.75),
}
