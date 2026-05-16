"""Extrae el texto del catálogo PDF de la empresa y lo limpia.

El catálogo está maquetado con kerning expandido (espacios entre cada letra),
así que extract_text() produce 'C A T A L O G O' en vez de 'CATALOGO'. Este
script lo detecta y compacta.

Salida: prompts/catalogo_empresa.txt — listo para enviar a Claude como
contexto de sistema (con prompt caching).

Uso:
    python scripts/preparar_catalogo.py
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pypdf import PdfReader  # noqa: E402

from src.config import PROJECT_ROOT  # noqa: E402
from src.logging_setup import configurar_logging  # noqa: E402

PDF_ENTRADA = PROJECT_ROOT / "prompts" / "catalogo_empresa.pdf"
TXT_SALIDA = PROJECT_ROOT / "prompts" / "catalogo_empresa.txt"


_PATRON_KERNING = re.compile(r"^\S(\s+\S)+\s*$")


def _es_linea_con_kerning(linea: str) -> bool:
    """True si la línea es: char + espacios + char + espacios + char + ...

    Detecta tanto líneas largas ('M O B I L I A R I O  U R B A N O') como
    cortas ('O R E Ñ A'). El patrón exige que cada elemento no-espacio sea
    UN único carácter — frases normales no encajan.
    """
    linea = linea.strip()
    if len(linea) < 3:
        return False
    return bool(_PATRON_KERNING.match(linea))


def _compactar_kerning(linea: str) -> str:
    """Convierte 'C A T A L O G O  i n f a n t i l' en 'CATALOGO infantil'.

    Regla:
    - Cadenas de 2+ espacios → marcador único ' '
    - Cadenas de 1 espacio rodeadas por caracteres → se eliminan
    """
    # Reemplazar 2+ espacios por un marcador único
    linea = re.sub(r" {2,}", "\x00", linea)
    # Eliminar espacios sueltos entre caracteres
    linea = linea.replace(" ", "")
    # Restituir los espacios "reales"
    linea = linea.replace("\x00", " ")
    return linea


def _limpiar_linea(linea: str) -> str:
    linea = linea.rstrip()
    if _es_linea_con_kerning(linea):
        return _compactar_kerning(linea)
    return linea


def main() -> int:
    log = configurar_logging("preparar_catalogo")
    if not PDF_ENTRADA.exists():
        log.error("No existe %s", PDF_ENTRADA)
        return 1

    log.info("Leyendo %s", PDF_ENTRADA)
    reader = PdfReader(str(PDF_ENTRADA))
    log.info("PDF con %d páginas", len(reader.pages))

    partes: list[str] = []
    for i, page in enumerate(reader.pages, 1):
        try:
            texto = page.extract_text() or ""
        except Exception as e:
            log.warning("Error en página %d: %s", i, e)
            continue
        lineas_limpias = [_limpiar_linea(l) for l in texto.split("\n")]
        # Quitar líneas vacías colapsadas
        lineas_limpias = [l for l in lineas_limpias if l.strip()]
        if lineas_limpias:
            partes.append(f"\n--- Página {i} ---\n" + "\n".join(lineas_limpias))

    texto_final = "\n".join(partes)
    TXT_SALIDA.write_text(texto_final, encoding="utf-8")
    log.info("Escrito %s (%d caracteres)", TXT_SALIDA, len(texto_final))
    log.info("Aprox %d tokens (estimación 4 chars/token)", len(texto_final) // 4)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
