"""Analiza una licitación con Claude: descarga pliegos, llama al cliente y
persiste el resultado en `analisis_ia`.
"""
from __future__ import annotations

import json
import logging
import sqlite3
from pathlib import Path
from typing import Iterable

from pydantic import BaseModel, Field, ValidationError, field_validator

from src.config import ANTHROPIC_MODEL, PROMPT_VERSION
from src.ia.cliente_claude import analizar as llamar_claude
from src.pliegos.descargador import descargar_pliegos_de_licitacion

log = logging.getLogger(__name__)


class AnalisisJSON(BaseModel):
    """Esquema esperado en la respuesta JSON de Claude."""
    encaje: str
    encaje_score: int = Field(ge=0, le=100)
    motivo: str
    resumen: str
    productos_aplicables: list[str] = Field(default_factory=list)
    requisitos_criticos: list[str] = Field(default_factory=list)
    banderas_rojas: list[str] = Field(default_factory=list)

    @field_validator("encaje")
    @classmethod
    def _normalizar_encaje(cls, v: str) -> str:
        valor = v.strip().lower()
        if valor not in {"alto", "medio", "bajo", "ninguno"}:
            raise ValueError(f"encaje inválido: {v}")
        return valor


def _resumen_estructurado(fila: sqlite3.Row) -> str:
    """Bloque de texto con los datos clave de la licitación."""
    partes = [
        f"Expediente: {fila['expediente']}",
        f"Objeto: {fila['objeto']}",
        f"Órgano contratante: {fila['organo_contratacion']}",
        f"Provincia: {fila['provincia'] or '(no detectada)'}",
        f"Municipio: {fila['municipio'] or '?'}",
        f"Importe sin IVA: {fila['importe_sin_iva']} {fila['moneda']}",
        f"Importe con IVA: {fila['importe_con_iva']} {fila['moneda']}",
        f"Tipo de contrato: {fila['tipo_contrato']}",
        f"Procedimiento: {fila['procedimiento']}",
        f"Duración: {fila['duracion_contrato']}",
        f"Fecha límite presentación: {fila['fecha_limite_presentacion']}",
        f"CPV principal: {fila['cpv_principal']}",
        f"CPVs secundarios: {fila['cpvs_secundarios']}",
        f"Lugar de ejecución: {fila['lugar_ejecucion']}",
        f"Enlace PLACSP: {fila['enlace_placsp']}",
    ]
    return "\n".join(p for p in partes if not p.endswith("None") and not p.endswith("?"))


def _rutas_pliegos_de(conn: sqlite3.Connection, licitacion_id: int) -> list[tuple[Path, str]]:
    """Devuelve [(ruta_local, etiqueta_para_claude), ...] de los pliegos
    descargados de esta licitación cuyo PDF está en disco. Excluye PCAP por
    política V1."""
    filas = conn.execute(
        """SELECT ruta_local, tipo_documento FROM pliegos_descargados
           WHERE licitacion_id = ? AND ruta_local IS NOT NULL
           ORDER BY CASE tipo_documento
                      WHEN 'PPT' THEN 1
                      WHEN 'anexo' THEN 2
                      ELSE 3
                    END""",
        (licitacion_id,),
    ).fetchall()
    salida: list[tuple[Path, str]] = []
    for f in filas:
        if f["tipo_documento"] == "PCAP":
            continue
        ruta = Path(f["ruta_local"])
        if not ruta.exists():
            continue
        salida.append((ruta, f"{f['tipo_documento']} - {ruta.name}"))
    return salida


def analizar_licitacion(conn: sqlite3.Connection, licitacion_id: int) -> dict:
    """Analiza una licitación completa. Devuelve dict con el resultado."""
    fila = conn.execute(
        "SELECT * FROM licitaciones WHERE id = ?", (licitacion_id,)
    ).fetchone()
    if fila is None:
        raise ValueError(f"Licitación {licitacion_id} no existe")

    # 1. Asegurar pliegos descargados (idempotente)
    descargar_pliegos_de_licitacion(conn, licitacion_id)

    # 2. Rutas a enviar
    pliegos = _rutas_pliegos_de(conn, licitacion_id)
    if not pliegos:
        log.warning("Licitación %s (%s) no tiene pliegos descargables", licitacion_id, fila["expediente"])
        return {"estado": "sin_pliegos"}

    # 3. Llamar a Claude
    resumen = _resumen_estructurado(fila)
    rutas = [p for p, _ in pliegos]
    titulos = [t for _, t in pliegos]
    resultado = llamar_claude(
        resumen_licitacion=resumen,
        pliegos=rutas,
        titulos_pliegos=titulos,
    )

    # 4. Validar JSON con Pydantic
    try:
        analisis = AnalisisJSON(**resultado.json_respuesta)
    except ValidationError as e:
        log.error("JSON inválido de Claude para licitación %s: %s", licitacion_id, e)
        # Guardamos la respuesta raw igualmente para auditoría
        conn.execute(
            """INSERT INTO analisis_ia
               (licitacion_id, modelo, prompt_version, respuesta_raw,
                tokens_entrada, tokens_salida, coste_usd)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                licitacion_id, ANTHROPIC_MODEL, PROMPT_VERSION,
                resultado.raw_text, resultado.tokens_entrada,
                resultado.tokens_salida, resultado.coste_usd,
            ),
        )
        return {"estado": "json_invalido", "error": str(e), "raw": resultado.raw_text}

    # 5. Persistir
    conn.execute(
        """INSERT INTO analisis_ia
           (licitacion_id, modelo, prompt_version, encaje, encaje_score,
            motivo, resumen, productos_aplicables, requisitos_criticos,
            banderas_rojas, respuesta_raw,
            tokens_entrada, tokens_salida, coste_usd)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            licitacion_id, ANTHROPIC_MODEL, PROMPT_VERSION,
            analisis.encaje, analisis.encaje_score, analisis.motivo, analisis.resumen,
            json.dumps(analisis.productos_aplicables, ensure_ascii=False),
            json.dumps(analisis.requisitos_criticos, ensure_ascii=False),
            json.dumps(analisis.banderas_rojas, ensure_ascii=False),
            json.dumps(resultado.json_respuesta, ensure_ascii=False),
            resultado.tokens_entrada, resultado.tokens_salida, resultado.coste_usd,
        ),
    )
    return {
        "estado": "ok",
        "encaje": analisis.encaje,
        "score": analisis.encaje_score,
        "coste_usd": resultado.coste_usd,
    }


def analizar_candidatas_pendientes(
    conn: sqlite3.Connection,
    *,
    max_licitaciones: int | None = None,
) -> Iterable[dict]:
    """Itera todas las licitaciones que pasan filtros y aún no tienen análisis
    con el prompt_version actual. Cada iteración produce el dict de
    analizar_licitacion()."""
    sql = """
        SELECT l.id, l.expediente
        FROM licitaciones l
        WHERE l.pasa_filtros = 1
          AND NOT EXISTS (
                SELECT 1 FROM analisis_ia a
                WHERE a.licitacion_id = l.id
                  AND a.prompt_version = ?
                  AND a.encaje IS NOT NULL
          )
        ORDER BY l.importe_sin_iva DESC NULLS LAST
    """
    filas = conn.execute(sql, (PROMPT_VERSION,)).fetchall()
    if max_licitaciones:
        filas = filas[:max_licitaciones]
    log.info("Pendientes de analizar: %d licitaciones", len(filas))
    for f in filas:
        log.info("Analizando id=%s expediente=%s", f["id"], f["expediente"])
        try:
            resultado = analizar_licitacion(conn, f["id"])
        except Exception as e:
            log.exception("Fallo analizando %s: %s", f["expediente"], e)
            resultado = {"estado": "error", "error": str(e)}
        yield {"licitacion_id": f["id"], "expediente": f["expediente"], **resultado}
