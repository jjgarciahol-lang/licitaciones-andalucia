"""Wrapper sobre el SDK de Anthropic con prompt caching del catálogo.

Estructura de cada llamada:

1. **System (cacheado, 1h TTL):**
   - Prompt de evaluación (`prompts/evaluacion_licitacion.md`).
   - Catálogo limpio de la empresa (`prompts/catalogo_empresa.txt`).
   Esta combinación apenas cambia entre llamadas — se cachea con
   `cache_control: ephemeral`, así sólo se paga al 100% la primera vez del
   día. Las siguientes lecturas cuestan ~10%.

2. **User (no cacheado):**
   - Resumen estructurado de la licitación (objeto, importe, plazo...).
   - PDFs nativos de los pliegos (PPT + anexos relevantes).
   - Recordatorio del formato de salida JSON.

Devuelve un dict con la respuesta parseada + métricas (tokens, coste).
"""
from __future__ import annotations

import base64
import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path

import anthropic

from src.config import (
    ANTHROPIC_API_KEY,
    ANTHROPIC_MODEL,
    PROJECT_ROOT,
    PROMPT_VERSION,
)

log = logging.getLogger(__name__)

PROMPT_PATH = PROJECT_ROOT / "prompts" / "evaluacion_licitacion.md"
CATALOGO_PATH = PROJECT_ROOT / "prompts" / "catalogo_empresa.txt"

# Precios Claude Sonnet 4.6 (USD por millón de tokens)
PRECIO_INPUT_USD_M = 3.00
PRECIO_OUTPUT_USD_M = 15.00
PRECIO_CACHE_WRITE_USD_M = 3.75   # primera escritura: 1.25x del input normal
PRECIO_CACHE_READ_USD_M = 0.30    # lecturas en hit: 10% del input


@dataclass
class ResultadoLlamada:
    json_respuesta: dict
    tokens_entrada: int
    tokens_salida: int
    tokens_cache_creacion: int
    tokens_cache_lectura: int
    coste_usd: float
    raw_text: str


def _cargar_system_blocks() -> list[dict]:
    """Construye los bloques de system con cache_control en el último."""
    prompt = PROMPT_PATH.read_text(encoding="utf-8")
    catalogo = CATALOGO_PATH.read_text(encoding="utf-8")
    contexto = (
        f"{prompt}\n\n"
        f"# CATÁLOGO DE PRODUCTOS DE HIGIOFI\n\n"
        f"A continuación va el catálogo completo (texto extraído del PDF). "
        f"Úsalo como única fuente sobre qué vendemos:\n\n"
        f"{catalogo}\n"
    )
    return [
        {
            "type": "text",
            "text": contexto,
            "cache_control": {"type": "ephemeral"},
        }
    ]


def _bloque_pdf(ruta: Path, nombre: str | None = None) -> dict:
    """Construye un bloque de tipo document con el PDF en base64."""
    contenido = base64.standard_b64encode(ruta.read_bytes()).decode("utf-8")
    return {
        "type": "document",
        "source": {
            "type": "base64",
            "media_type": "application/pdf",
            "data": contenido,
        },
        **({"title": nombre} if nombre else {}),
    }


def _extraer_json(texto: str) -> dict:
    """Intenta extraer el JSON de la respuesta de Claude.

    El prompt le pide JSON limpio, pero por si acaso aceptamos también
    bloques ```json ... ``` o texto con JSON dentro."""
    texto = texto.strip()
    if texto.startswith("{"):
        try:
            return json.loads(texto)
        except json.JSONDecodeError:
            pass
    bloque = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", texto, re.DOTALL)
    if bloque:
        return json.loads(bloque.group(1))
    # Última oportunidad: encontrar el primer { y el último }
    inicio = texto.find("{")
    fin = texto.rfind("}")
    if inicio >= 0 and fin > inicio:
        return json.loads(texto[inicio:fin + 1])
    raise ValueError(f"No pude extraer JSON de la respuesta: {texto[:300]}")


def _calcular_coste(usage) -> float:
    """Coste en USD a partir del bloque usage del SDK Anthropic."""
    in_normal = (usage.input_tokens or 0) - (getattr(usage, "cache_creation_input_tokens", 0) or 0) - (getattr(usage, "cache_read_input_tokens", 0) or 0)
    cache_write = getattr(usage, "cache_creation_input_tokens", 0) or 0
    cache_read = getattr(usage, "cache_read_input_tokens", 0) or 0
    out = usage.output_tokens or 0

    coste = (
        in_normal * PRECIO_INPUT_USD_M
        + cache_write * PRECIO_CACHE_WRITE_USD_M
        + cache_read * PRECIO_CACHE_READ_USD_M
        + out * PRECIO_OUTPUT_USD_M
    ) / 1_000_000
    return round(coste, 5)


def analizar(
    *,
    resumen_licitacion: str,
    pliegos: list[Path],
    titulos_pliegos: list[str] | None = None,
    max_paginas_pliego: int | None = None,  # reservado para futuro
) -> ResultadoLlamada:
    """Llama a Claude para evaluar una licitación.

    `resumen_licitacion`: bloque de texto con los datos estructurados
    (expediente, órgano, importe, plazo, CPV...).
    `pliegos`: lista de rutas a PDFs que se mandan como documento nativo.
    """
    if not ANTHROPIC_API_KEY:
        raise RuntimeError("ANTHROPIC_API_KEY no configurada en .env")

    cliente = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    system_blocks = _cargar_system_blocks()

    user_content: list[dict] = []
    titulos = titulos_pliegos or [p.name for p in pliegos]
    for ruta, titulo in zip(pliegos, titulos):
        user_content.append(_bloque_pdf(ruta, titulo))
    user_content.append({
        "type": "text",
        "text": (
            f"## Datos estructurados de la licitación\n\n{resumen_licitacion}\n\n"
            "## Tarea\n\n"
            "Lee los pliegos adjuntos arriba y devuelve únicamente el JSON "
            "con el formato indicado en las instrucciones del system."
        ),
    })

    log.info("Llamando a Claude (%s, prompt v%s)", ANTHROPIC_MODEL, PROMPT_VERSION)
    respuesta = cliente.messages.create(
        model=ANTHROPIC_MODEL,
        max_tokens=2000,
        system=system_blocks,
        messages=[{"role": "user", "content": user_content}],
    )

    texto = "".join(b.text for b in respuesta.content if b.type == "text")
    datos = _extraer_json(texto)
    coste = _calcular_coste(respuesta.usage)
    log.info(
        "  in=%d (cache_w=%d, cache_r=%d) out=%d coste=$%.4f",
        respuesta.usage.input_tokens or 0,
        getattr(respuesta.usage, "cache_creation_input_tokens", 0) or 0,
        getattr(respuesta.usage, "cache_read_input_tokens", 0) or 0,
        respuesta.usage.output_tokens or 0,
        coste,
    )
    return ResultadoLlamada(
        json_respuesta=datos,
        tokens_entrada=respuesta.usage.input_tokens or 0,
        tokens_salida=respuesta.usage.output_tokens or 0,
        tokens_cache_creacion=getattr(respuesta.usage, "cache_creation_input_tokens", 0) or 0,
        tokens_cache_lectura=getattr(respuesta.usage, "cache_read_input_tokens", 0) or 0,
        coste_usd=coste,
        raw_text=texto,
    )
