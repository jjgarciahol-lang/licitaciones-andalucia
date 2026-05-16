"""Descarga de PDFs de pliego (PCAP, PPT, anexos) desde las URLs que vienen
en cada licitación.

Reglas:
- Guardamos en data/pliegos/{año-mes}/{expediente_sanitizado}/{tipo}-{nombre}.pdf
- Deduplicamos por sha256: si el mismo PDF ya está descargado para esa
  licitación, no rebajamos.
- Registramos en la tabla `pliegos_descargados` con metadatos.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import sqlite3
from pathlib import Path

import requests
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from src.config import PLACSP_USER_AGENT, PLIEGOS_DIR
from src.modelos import Documento

log = logging.getLogger(__name__)

TIMEOUT = (10, 180)
TAMANO_MAX_MB = 50  # PDFs más grandes que esto no se descargan (raros y problemáticos)


def _slug(texto: str, max_len: int = 60) -> str:
    """Convierte un expediente en un nombre de carpeta seguro."""
    texto = re.sub(r"[^\w\-\.]", "_", texto)
    texto = re.sub(r"_+", "_", texto).strip("_.")
    return texto[:max_len] or "sin_expediente"


def _sha256_de_fichero(ruta: Path) -> str:
    h = hashlib.sha256()
    with ruta.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 64), b""):
            h.update(chunk)
    return h.hexdigest()


@retry(
    retry=retry_if_exception_type((requests.ConnectionError, requests.Timeout)),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=2, min=2, max=20),
    reraise=True,
)
def _descargar(url: str, destino: Path) -> int:
    cabeceras = {"User-Agent": PLACSP_USER_AGENT, "Accept": "application/pdf,*/*"}
    with requests.get(url, headers=cabeceras, stream=True, timeout=TIMEOUT) as r:
        r.raise_for_status()
        tamano = int(r.headers.get("content-length", 0))
        if tamano and tamano > TAMANO_MAX_MB * 1_048_576:
            raise RuntimeError(f"PDF demasiado grande ({tamano / 1_048_576:.1f} MB > {TAMANO_MAX_MB})")
        tmp = destino.with_suffix(destino.suffix + ".part")
        destino.parent.mkdir(parents=True, exist_ok=True)
        with tmp.open("wb") as f:
            for chunk in r.iter_content(chunk_size=1024 * 64):
                if chunk:
                    f.write(chunk)
        tmp.replace(destino)
    return destino.stat().st_size


def descargar_pliego(
    conn: sqlite3.Connection,
    licitacion_id: int,
    expediente: str,
    fecha_publicacion: str | None,
    documento: Documento,
) -> dict:
    """Descarga un único documento y registra en pliegos_descargados.

    Devuelve dict con: {estado, ruta_local, sha256, error}.
    """
    yyyy_mm = (fecha_publicacion or "0000-00")[:7]
    carpeta = PLIEGOS_DIR / yyyy_mm / _slug(expediente)
    nombre_base = _slug(documento.nombre or documento.tipo or "doc", max_len=80)
    if not nombre_base.lower().endswith(".pdf"):
        nombre_base += ".pdf"
    destino = carpeta / f"{documento.tipo or 'otros'}-{nombre_base}"

    try:
        if not destino.exists():
            tam = _descargar(documento.url, destino)
        else:
            tam = destino.stat().st_size
        sha = _sha256_de_fichero(destino)
    except Exception as e:
        log.warning("No pude descargar %s: %s", documento.url, e)
        conn.execute(
            """INSERT INTO pliegos_descargados
               (licitacion_id, tipo_documento, url_origen, error)
               VALUES (?, ?, ?, ?)""",
            (licitacion_id, documento.tipo, documento.url, str(e)),
        )
        return {"estado": "error", "ruta_local": None, "sha256": None, "error": str(e)}

    # Comprobar si ese sha ya está registrado para esta licitación
    ya = conn.execute(
        "SELECT id FROM pliegos_descargados WHERE licitacion_id = ? AND sha256 = ?",
        (licitacion_id, sha),
    ).fetchone()
    if ya:
        return {"estado": "ya_existia", "ruta_local": str(destino), "sha256": sha, "error": None}

    # Páginas: lo dejamos None aquí, lo rellena el extractor
    conn.execute(
        """INSERT INTO pliegos_descargados
           (licitacion_id, tipo_documento, url_origen, ruta_local, tamano_bytes,
            sha256, extraccion_ok)
           VALUES (?, ?, ?, ?, ?, ?, 0)""",
        (licitacion_id, documento.tipo, documento.url, str(destino), tam, sha),
    )
    return {"estado": "descargado", "ruta_local": str(destino), "sha256": sha, "error": None}


def descargar_pliegos_de_licitacion(
    conn: sqlite3.Connection,
    licitacion_id: int,
    *,
    omitir_tipos: tuple[str, ...] = ("PCAP",),
) -> list[dict]:
    """Descarga todos los documentos de una licitación (omitiendo los tipos
    indicados — por defecto el PCAP, según política V1)."""
    fila = conn.execute(
        "SELECT expediente, fecha_publicacion, documentos_urls FROM licitaciones WHERE id = ?",
        (licitacion_id,),
    ).fetchone()
    if fila is None:
        return []
    docs_json = fila["documentos_urls"]
    if not docs_json:
        return []
    docs_raw = json.loads(docs_json)
    documentos = [Documento(url=d["url"], tipo=d.get("tipo"), nombre=d.get("nombre")) for d in docs_raw]
    resultados = []
    for d in documentos:
        if d.tipo in omitir_tipos:
            continue
        resultados.append(
            descargar_pliego(conn, licitacion_id, fila["expediente"], fila["fecha_publicacion"], d)
        )
    return resultados
