"""Extracción de texto desde PDFs descargados.

Importante: PLACSP a veces sirve ficheros `.doc`/`.docx` con extensión `.pdf`.
Detectamos esos casos por magic bytes (los PDFs empiezan por `%PDF`) y los
rechazamos antes de intentar parsearlos con pypdf, que daría un error feo.
"""
from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

from pypdf import PdfReader

log = logging.getLogger(__name__)

MAGIC_PDF = b"%PDF"


class ExtraccionResult:
    __slots__ = ("ok", "paginas", "texto", "ruta_txt", "error")

    def __init__(self, ok: bool, paginas: int = 0, texto: str = "",
                 ruta_txt: Path | None = None, error: str | None = None):
        self.ok = ok
        self.paginas = paginas
        self.texto = texto
        self.ruta_txt = ruta_txt
        self.error = error


def _es_pdf_real(ruta: Path) -> bool:
    """Comprueba los primeros 4 bytes."""
    try:
        with ruta.open("rb") as f:
            return f.read(4) == MAGIC_PDF
    except OSError:
        return False


def extraer_a_txt(ruta_pdf: Path, *, escribir_txt: bool = True) -> ExtraccionResult:
    """Extrae texto del PDF y lo escribe a un .txt junto al original.

    Devuelve ExtraccionResult con metadata. No toca la DB — para eso usa
    `extraer_y_persistir`.
    """
    if not ruta_pdf.exists():
        return ExtraccionResult(False, error="Fichero no existe")
    if not _es_pdf_real(ruta_pdf):
        return ExtraccionResult(False, error="No es PDF válido (probable .doc/.docx con extensión falsa)")

    try:
        reader = PdfReader(str(ruta_pdf))
        n = len(reader.pages)
        textos: list[str] = []
        for i, page in enumerate(reader.pages, 1):
            try:
                textos.append(page.extract_text() or "")
            except Exception as e:
                log.debug("Error en página %d de %s: %s", i, ruta_pdf.name, e)
        texto = "\n--PAG--\n".join(textos)
    except Exception as e:
        return ExtraccionResult(False, error=f"Error pypdf: {e}")

    ruta_txt = None
    if escribir_txt:
        ruta_txt = ruta_pdf.with_suffix(".txt")
        try:
            ruta_txt.write_text(texto, encoding="utf-8")
        except OSError as e:
            return ExtraccionResult(False, paginas=n, texto=texto,
                                    error=f"No pude escribir .txt: {e}")

    return ExtraccionResult(True, paginas=n, texto=texto, ruta_txt=ruta_txt)


def extraer_y_persistir(
    conn: sqlite3.Connection,
    licitacion_id: int,
    ruta_pdf: Path,
) -> ExtraccionResult:
    """Extrae texto y actualiza la fila de `pliegos_descargados` correspondiente."""
    res = extraer_a_txt(ruta_pdf, escribir_txt=True)
    conn.execute(
        """UPDATE pliegos_descargados
           SET paginas = ?, texto_extraido_path = ?, extraccion_ok = ?, error = ?
           WHERE licitacion_id = ? AND ruta_local = ?""",
        (
            res.paginas or None,
            str(res.ruta_txt) if res.ruta_txt else None,
            1 if res.ok else 0,
            res.error,
            licitacion_id,
            str(ruta_pdf),
        ),
    )
    return res
