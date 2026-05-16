"""Carga de configuración desde .env y constantes del proyecto."""
from __future__ import annotations

import os
import unicodedata
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")


def _env(name: str, default: str | None = None, required: bool = False) -> str:
    value = os.getenv(name, default)
    if required and not value:
        raise RuntimeError(f"Variable de entorno requerida no definida: {name}")
    return value or ""


def _env_path(name: str, default: str) -> Path:
    raw = os.getenv(name, default)
    path = Path(raw)
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return path.resolve()


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    return float(raw) if raw else default


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    return int(raw) if raw else default


def _normalizar(texto: str) -> str:
    """Minúsculas sin tildes ni espacios laterales, para comparar nombres de provincia."""
    sin_tildes = unicodedata.normalize("NFKD", texto)
    sin_tildes = "".join(c for c in sin_tildes if not unicodedata.combining(c))
    return sin_tildes.strip().lower()


# --- Anthropic ----------------------------------------------------------------
ANTHROPIC_API_KEY = _env("ANTHROPIC_API_KEY")
ANTHROPIC_MODEL = _env("ANTHROPIC_MODEL", "claude-sonnet-4-6")

# --- Airtable -----------------------------------------------------------------
AIRTABLE_API_KEY = _env("AIRTABLE_API_KEY")
AIRTABLE_BASE_ID = _env("AIRTABLE_BASE_ID")
AIRTABLE_TABLE_LICITACIONES = _env("AIRTABLE_TABLE_LICITACIONES", "Licitaciones")

# --- Resend -------------------------------------------------------------------
RESEND_API_KEY = _env("RESEND_API_KEY")
EMAIL_FROM = _env("EMAIL_FROM", "onboarding@resend.dev")
EMAIL_FROM_NAME = _env("EMAIL_FROM_NAME", "Bot Licitaciones")
EMAIL_TO = [x.strip() for x in _env("EMAIL_TO").split(",") if x.strip()]

# --- PLACSP -------------------------------------------------------------------
PLACSP_BASE_URL = _env(
    "PLACSP_BASE_URL",
    "https://contrataciondelsectorpublico.gob.es/sindicacion/sindicacion_643",
)
PLACSP_USER_AGENT = _env(
    "PLACSP_USER_AGENT",
    "LicitacionesBot/1.0 (contacto@example.com)",
)

# --- Rutas --------------------------------------------------------------------
DB_PATH = _env_path("DB_PATH", "./data/licitaciones.db")
ZIPS_DIR = _env_path("ZIPS_DIR", "./data/zips")
PLIEGOS_DIR = _env_path("PLIEGOS_DIR", "./data/pliegos")
LOG_DIR = _env_path("LOG_DIR", "./logs")
LOG_LEVEL = _env("LOG_LEVEL", "INFO").upper()

for _d in (ZIPS_DIR, PLIEGOS_DIR, LOG_DIR, DB_PATH.parent):
    _d.mkdir(parents=True, exist_ok=True)

# --- Filtros duros ------------------------------------------------------------
IMPORTE_MIN = _env_float("IMPORTE_MIN", 1_000.0)
IMPORTE_MAX = _env_float("IMPORTE_MAX", 500_000.0)

# Almería excluida en V1: queda demasiado lejos del centro logístico (decisión comercial).
_PROVINCIAS_DEFAULT = "Cádiz,Sevilla,Málaga,Granada,Jaén,Córdoba,Huelva"
PROVINCIAS_PERMITIDAS = {
    _normalizar(p) for p in _env("PROVINCIAS_PERMITIDAS", _PROVINCIAS_DEFAULT).split(",") if p.strip()
}

# CPVs relevantes para el catálogo (mobiliario urbano, parques infantiles,
# material escolar y áreas afines). Se evalúan como PREFIJOS: un CPV de la
# licitación pasa el filtro si EMPIEZA por alguno de los strings de aquí.
#
# Filosofía: somos generosos en el filtro CPV para no perder licitaciones que
# las administraciones etiquetan con códigos genéricos (39100, 45236...). El
# análisis posterior con Claude se encarga de descartar las que no encajan
# realmente con el catálogo de la empresa.
CPVS_RELEVANTES: tuple[str, ...] = (
    # --- Mobiliario urbano (núcleo del catálogo) ----------------------------
    "34928",     # 34928xxx — Mobiliario urbano: bancos, papeleras, jardineras,
                 # vallas, barandillas, marquesinas, aparcabicis, fuentes,
                 # señalización vertical, contenedores, etc.

    # --- Parques infantiles (núcleo del catálogo) ---------------------------
    "37535",     # 37535xxx — Equipos para áreas de juego infantil: columpios,
                 # toboganes, balancines, carruseles, casitas, etc.
    "43325",     # 43325xxx — Equipamiento para parques y zonas de juego
                 # (algunos órganos lo prefieren a 37535).

    # --- Aparatos biosaludables / deporte al aire libre ---------------------
    "37440",     # 37440xxx — Equipos de fitness (biosaludables exteriores).
    "37441",     # 37441xxx — Equipos de aeróbic.
    "37442",     # 37442xxx — Bancos de musculación / pesas.
    "37410",     # 37410xxx — Equipos para deportes al aire libre.
    "37451",     # 37451xxx — Equipos para deportes de campo (porterías,
                 # canastas exteriores, etc.).
    "37416",     # 37416xxx — Equipos de ocio.

    # --- Pavimentación y obra asociada a parques/áreas deportivas -----------
    "45236210",  # Pavimentación de zonas de juego infantil (caucho, etc.).
    "45236200",  # Pavimentación de instalaciones deportivas.
    "45112723",  # Trabajos de paisajismo en zonas de juego.

    # --- Material y mobiliario escolar (núcleo del catálogo) ----------------
    "39160",     # 39160xxx — Mobiliario escolar (mesas, sillas, pizarras,
                 # armarios, taquillas, gradas escolares).
    "39161",     # 39161xxx — Mobiliario para guarderías / educación infantil.
    "39162",     # 39162xxx — Equipo educativo, equipo pedagógico, material
                 # didáctico, material escolar.
    "39163",     # 39163xxx — Mobiliario y equipos escolares varios.

    # --- Mobiliario general (a menudo cubre nuestras categorías) ------------
    # Estos prefijos son amplios y traerán "ruido" — pero también traerán
    # licitaciones de mobiliario urbano / escolar etiquetadas de forma
    # genérica. El filtro IA depura.
    "39100",     # Mobiliario general.
    "39110",     # Asientos, sillas (escolares, bancos, etc.).
    "39120",     # Mesas, armarios, escritorios, estanterías.
    "39150",     # Mobiliario y equipo diverso.
    "39151",     # Mobiliario diverso (muy común para mobiliario urbano).

    # --- Material de oficina/papelería (colegios y centros educativos) ------
    "30192",     # Material de oficina (en lotes para colegios).
    "30190",     # Material de oficina diverso.
)

# --- IA -----------------------------------------------------------------------
PROMPT_VERSION = _env("PROMPT_VERSION", "v1")
MAX_PDF_PAGES = _env_int("MAX_PDF_PAGES", 80)
COSTE_MAX_DIARIO_USD = _env_float("COSTE_MAX_DIARIO_USD", 5.0)

# --- Programación -------------------------------------------------------------
HORA_ENVIO_RESUMEN = _env("HORA_ENVIO_RESUMEN", "07:00")
TIMEZONE = _env("TIMEZONE", "Europe/Madrid")


def es_provincia_permitida(nombre: str | None) -> bool:
    """True si `nombre` (con o sin tildes, mayúsculas/minúsculas) está en la lista."""
    if not nombre:
        return False
    return _normalizar(nombre) in PROVINCIAS_PERMITIDAS


def es_cpv_relevante(cpv: str | None) -> bool:
    """True si el CPV empieza por alguno de los prefijos relevantes."""
    if not cpv:
        return False
    cpv = cpv.strip()
    return any(cpv.startswith(prefijo) for prefijo in CPVS_RELEVANTES)
