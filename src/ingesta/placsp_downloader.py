"""Descarga de ZIPs mensuales de licitaciones desde PLACSP.

Cada URL apunta a un fichero mensual con todas las licitaciones publicadas en
ese mes, p. ej.:

    https://contrataciondelsectorpublico.gob.es/sindicacion/sindicacion_643/
    licitacionesPerfilesContratanteCompleto3_202605.zip

El downloader es idempotente: si el ZIP ya está en disco y no se fuerza la
redescarga, no vuelve a bajarlo. Para el mes en curso conviene siempre forzar
la descarga, porque se actualiza a diario con licitaciones nuevas.
"""
from __future__ import annotations

import logging
from datetime import date
from pathlib import Path

import requests
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from src.config import PLACSP_BASE_URL, PLACSP_USER_AGENT, ZIPS_DIR

log = logging.getLogger(__name__)

NOMBRE_ZIP = "licitacionesPerfilesContratanteCompleto3_{aaaamm}.zip"
TIMEOUT_DESCARGA = (10, 600)  # (conexión, lectura) segundos


def url_mes(year: int, month: int) -> str:
    """URL del ZIP mensual de PLACSP."""
    aaaamm = f"{year:04d}{month:02d}"
    return f"{PLACSP_BASE_URL}/{NOMBRE_ZIP.format(aaaamm=aaaamm)}"


def ruta_local(year: int, month: int) -> Path:
    """Ruta donde se guarda el ZIP localmente."""
    return ZIPS_DIR / NOMBRE_ZIP.format(aaaamm=f"{year:04d}{month:02d}")


def _es_zip_valido(ruta: Path) -> bool:
    """Comprobación barata: el ZIP empieza con 'PK\\x03\\x04' y tiene tamaño > 0."""
    if not ruta.is_file() or ruta.stat().st_size == 0:
        return False
    try:
        with ruta.open("rb") as f:
            return f.read(4) == b"PK\x03\x04"
    except OSError:
        return False


@retry(
    retry=retry_if_exception_type((requests.ConnectionError, requests.Timeout)),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=2, min=2, max=30),
    reraise=True,
)
def _descargar_a(url: str, destino: Path) -> int:
    """Descarga `url` a `destino` con stream. Devuelve bytes escritos."""
    cabeceras = {"User-Agent": PLACSP_USER_AGENT, "Accept": "application/zip,*/*"}
    log.info("GET %s", url)
    with requests.get(url, headers=cabeceras, stream=True, timeout=TIMEOUT_DESCARGA) as r:
        r.raise_for_status()
        tmp = destino.with_suffix(destino.suffix + ".part")
        bytes_escritos = 0
        with tmp.open("wb") as f:
            for chunk in r.iter_content(chunk_size=1024 * 256):
                if chunk:
                    f.write(chunk)
                    bytes_escritos += len(chunk)
        tmp.replace(destino)
    return bytes_escritos


def descargar_mes(year: int, month: int, *, force: bool = False) -> Path:
    """Descarga el ZIP mensual y devuelve la ruta local.

    - Si el ZIP ya existe y es válido, no redescarga (salvo `force=True`).
    - Para el mes en curso conviene `force=True` (se actualiza a diario).
    """
    destino = ruta_local(year, month)
    if not force and _es_zip_valido(destino):
        log.info("ZIP %s ya en disco (%.1f MB), salto descarga", destino.name,
                 destino.stat().st_size / 1_048_576)
        return destino

    url = url_mes(year, month)
    destino.parent.mkdir(parents=True, exist_ok=True)
    bytes_escritos = _descargar_a(url, destino)
    log.info("Descargado %s (%.1f MB)", destino.name, bytes_escritos / 1_048_576)

    if not _es_zip_valido(destino):
        destino.unlink(missing_ok=True)
        raise RuntimeError(f"Descarga corrupta: {url}")

    return destino


def meses_a_procesar(meses_atras: int, hoy: date | None = None) -> list[tuple[int, int]]:
    """Lista de (año, mes) para el mes actual y los `meses_atras` anteriores.

    Ejemplo: hoy=2026-05-16, meses_atras=3 -> [(2026,2), (2026,3), (2026,4), (2026,5)]
    """
    hoy = hoy or date.today()
    y, m = hoy.year, hoy.month
    resultado: list[tuple[int, int]] = []
    for _ in range(meses_atras + 1):
        resultado.append((y, m))
        m -= 1
        if m == 0:
            m = 12
            y -= 1
    return list(reversed(resultado))
