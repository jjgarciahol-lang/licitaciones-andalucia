"""Ingesta del Directorio de Centros Docentes de la Junta de Andalucía.

Lee el CSV oficial (cacheado en `data/mapa/raw/da_centros.csv`), filtra por las
provincias activas (`PROVINCIAS_MAPA`), mapea a `ClientePotencial` y hace upsert
en la tabla `clientes`.

Decisiones de mapeo:

- `D_TIPO == "Público"` → `colegio_publico`
- `D_TIPO == "Privado"` + `D_DENOMINA == "Centro de Educación Infantil"` → `guarderia`
  (son CEI autorizados por la Junta, 1er ciclo 0-3 años — clientes-objetivo
  reales para Higiofi: mobiliario infantil y pequeño material de papelería).
- `D_TIPO == "Privado"` + resto de denominaciones → `colegio_privado`.

NOTA: el dataset NO permite distinguir concertados de no concertados (las
columnas `priv_c_*` y `priv_noc_*` de unidades vienen vacías en TODOS los
centros privados, solo se rellenan en públicos). La comercial puede marcarlo
a mano en el estado interno si lo necesita.

- `etapas` se construye agregando columnas de unidades por etapa (cualquier
  valor > 0 en columnas `*_inf1`, `*_inf2`, `*_pri`, `*_eso`, `*_bach*`,
  `*_fp*`/`*_cfpg*`/`*_cfgbas`/`*_ce_g*`, `*_ee`, `*_adul*`/`*_planes*`,
  `*_idi*`, `*_apd`/`*_ESCR`/`*_ESD`/`*_Master_EA`/`*_Dra`/`*_Ens_Mus`/`*_Ens_Dan`,
  `*_Dep_*`). El frontend usa estas etiquetas para filtrar. Solo se rellena en
  centros públicos (los privados no reportan unidades en el dataset).
"""
from __future__ import annotations

import csv
import hashlib
import logging
from pathlib import Path
from typing import Iterable

import requests

from src.mapa.config_mapa import (
    JUNTA_CENTROS_CSV_LOCAL,
    JUNTA_CENTROS_CSV_URL,
    NOMINATIM_USER_AGENT,
    PROVINCIAS_MAPA,
)
from src.mapa.db import conexion
from src.mapa.modelos import ClientePotencial


log = logging.getLogger(__name__)


# Prefijos/sufijos de columnas por etapa. Hacemos endswith() porque las columnas
# son `pub_*`, `priv_c_*`, `priv_noc_*` con la misma raíz por etapa.
ETAPAS_SUFIJOS: dict[str, tuple[str, ...]] = {
    "EI":   ("_inf1", "_inf2"),
    "EP":   ("_pri",),
    "ESO":  ("_eso",),
    "BACH": ("_bach_ord", "_bach_adul", "_bach_semi_dist"),
    "FP":   ("_fpbasica", "_fpbas", "_cfpgm_ord", "_cfpgm_adul", "_cfpgm_semi_dist",
             "_cfpgs_ord", "_cfpgs_adul", "_cfpgs_semi_dist", "_cfgbas",
             "_ce_gm", "_ce_gs"),
    "EE":   ("_ee",),
    "ADUL": ("_Adul_formal", "_planes"),
    "IDI":  ("_idi", "_idi_libre", "_Ens_no_reg_idi"),
    "ART":  ("_apd", "_ESCR", "_ESD", "_Master_EA", "_Dra", "_Ens_Mus",
             "_Ens_Dan", "_Ens_no_reg_mus", "_Ens_no_reg_dan"),
    "DEP":  ("_Dep_gm", "_Dep_gs"),
}

# Denominaciones (`D_DENOMINA`) que identifican centros de educación infantil
# de 1er ciclo — para Higiofi son guarderías a efectos comerciales.
DENOMINACIONES_GUARDERIA = (
    "centro de educación infantil",
    "centro de educacion infantil",  # por si llega sin tilde
)


def descargar_csv(force: bool = False) -> Path:
    """Descarga el CSV de centros si no existe localmente o se fuerza."""
    destino = JUNTA_CENTROS_CSV_LOCAL
    if destino.exists() and not force:
        log.info("CSV ya descargado en %s (usa force=True para refrescar)", destino)
        return destino

    log.info("Descargando CSV desde %s", JUNTA_CENTROS_CSV_URL)
    resp = requests.get(
        JUNTA_CENTROS_CSV_URL,
        headers={"User-Agent": NOMINATIM_USER_AGENT},
        timeout=60,
    )
    resp.raise_for_status()
    destino.write_bytes(resp.content)
    log.info("Descargados %d KB → %s", len(resp.content) // 1024, destino)
    return destino


def _parse_float(s: str | None) -> float | None:
    if not s:
        return None
    s = s.strip().replace(",", ".")
    if not s or s.lower() in ("null", "n/a", "-"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _etapas_activas(fila: dict[str, str]) -> str:
    """Devuelve un CSV con las etapas activas.

    Las columnas de etapa (`pub_*`, `priv_c_*`, `priv_noc_*`) son flags 'S'/'N',
    no enteros — pese a que la nomenclatura las documenta como "Número".
    Si alguna columna que termina en uno de los sufijos de la etapa vale 'S',
    la etapa está activa.
    """
    activas: list[str] = []
    for etapa, sufijos in ETAPAS_SUFIJOS.items():
        for col, valor in fila.items():
            if col is None:
                continue
            if col.endswith(sufijos) and (valor or "").strip().upper() == "S":
                activas.append(etapa)
                break
    return ",".join(activas)


def _clasificar_tipo(fila: dict[str, str]) -> str:
    """Devuelve `colegio_publico` / `guarderia` / `colegio_privado`.

    El dataset no permite derivar `colegio_concertado` (ver docstring del módulo).
    """
    titularidad = (fila.get("D_TIPO") or "").strip().lower()
    if titularidad.startswith("públ") or titularidad.startswith("publ"):
        return "colegio_publico"
    denom = (fila.get("D_DENOMINA") or "").strip().lower()
    if any(d in denom for d in DENOMINACIONES_GUARDERIA):
        return "guarderia"
    return "colegio_privado"


def _id_estable(codigo: str) -> str:
    """ID estable: SHA1 de (fuente, codigo). Reejecutar la ingesta no duplica."""
    return hashlib.sha1(f"junta_andalucia:{codigo}".encode("utf-8")).hexdigest()[:16]


def _fila_a_cliente(fila: dict[str, str]) -> ClientePotencial | None:
    """Convierte una fila del CSV de la Junta en un `ClientePotencial`.

    Devuelve `None` si la fila no cumple requisitos mínimos (sin código o
    sin nombre).
    """
    codigo = (fila.get("codigo") or "").strip()
    if not codigo:
        return None

    # Nombre: la Junta separa "denominación" (CEIP, IES, ...) de "específica"
    # (nombre del centro). Concatenamos para que la comercial vea ambos.
    denom = (fila.get("D_DENOMINA") or "").strip()
    especifica = (fila.get("D_ESPECIFICA") or "").strip()
    if not especifica and not denom:
        return None
    nombre = f"{denom} {especifica}".strip() if denom else especifica

    return ClientePotencial(
        id=_id_estable(codigo),
        tipo=_clasificar_tipo(fila),
        nombre=nombre,
        direccion=(fila.get("D_DOMICILIO") or "").strip() or None,
        municipio=(fila.get("D_MUNICIPIO") or "").strip() or None,
        provincia=(fila.get("D_PROVINCIA") or "").strip(),
        cp=(fila.get("C_POSTAL") or "").strip() or None,
        telefono=(fila.get("N_TELEFONO") or "").strip() or None,
        email=(fila.get("Correo_e") or "").strip() or None,
        web=None,  # el dataset no incluye web
        lat=_parse_float(fila.get("N_LATITUD")),
        lon=_parse_float(fila.get("N_LONGITUD")),
        etapas=_etapas_activas(fila) or None,
        codigo_origen=codigo,
        fuente="junta_andalucia",
        confianza="alta",
    )


def _leer_csv(path: Path) -> Iterable[dict[str, str]]:
    """Itera las filas del CSV de la Junta (UTF-8, separador ';')."""
    with path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f, delimiter=";")
        yield from reader


def upsert_cliente(conn, cliente: ClientePotencial) -> str:
    """Inserta o actualiza un cliente por id. Devuelve 'nuevo' o 'actualizado'."""
    existe = conn.execute("SELECT 1 FROM clientes WHERE id = ?", (cliente.id,)).fetchone()
    conn.execute(
        """
        INSERT INTO clientes (
            id, tipo, nombre, direccion, municipio, provincia, cp,
            telefono, email, web, lat, lon, etapas, fuente, codigo_origen,
            confianza, fecha_actualizacion
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,datetime('now'))
        ON CONFLICT(id) DO UPDATE SET
            tipo=excluded.tipo,
            nombre=excluded.nombre,
            direccion=excluded.direccion,
            municipio=excluded.municipio,
            provincia=excluded.provincia,
            cp=excluded.cp,
            telefono=excluded.telefono,
            email=excluded.email,
            web=excluded.web,
            lat=excluded.lat,
            lon=excluded.lon,
            etapas=excluded.etapas,
            fuente=excluded.fuente,
            codigo_origen=excluded.codigo_origen,
            confianza=excluded.confianza,
            fecha_actualizacion=datetime('now')
        """,
        (
            cliente.id, cliente.tipo, cliente.nombre, cliente.direccion,
            cliente.municipio, cliente.provincia, cliente.cp, cliente.telefono,
            cliente.email, cliente.web, cliente.lat, cliente.lon,
            cliente.etapas, cliente.fuente, cliente.codigo_origen,
            cliente.confianza,
        ),
    )
    return "actualizado" if existe else "nuevo"


def ingestar(force_download: bool = False) -> dict[str, int]:
    """Pipeline completo: descarga + parsing + filtro + upsert.

    Devuelve resumen con conteos para logging y validación.
    """
    csv_path = descargar_csv(force=force_download)
    provincias_objetivo = {p.lower() for p in PROVINCIAS_MAPA}

    leidos = 0
    fuera_provincia = 0
    sin_codigo = 0
    nuevos = 0
    actualizados = 0
    sin_coordenadas = 0

    with conexion() as conn:
        conn.execute("BEGIN")
        try:
            for fila in _leer_csv(csv_path):
                leidos += 1
                provincia = (fila.get("D_PROVINCIA") or "").strip().lower()
                if provincia not in provincias_objetivo:
                    fuera_provincia += 1
                    continue

                cliente = _fila_a_cliente(fila)
                if cliente is None:
                    sin_codigo += 1
                    continue

                if cliente.lat is None or cliente.lon is None:
                    sin_coordenadas += 1

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
        "leidos": leidos,
        "fuera_provincia": fuera_provincia,
        "sin_codigo": sin_codigo,
        "nuevos": nuevos,
        "actualizados": actualizados,
        "sin_coordenadas": sin_coordenadas,
    }
