"""Upsert de licitaciones en SQLite.

La clave única es `uuid_placsp`. El comportamiento es idempotente:

- Si la licitación no existe: INSERT + evaluación de filtros.
- Si existe: comparamos `hash_contenido`.
  - Igual                       → sólo actualizamos `fecha_ultima_actualizacion`.
  - Distinto pero cambios menores (p.ej. nueva fecha límite) → UPDATE, sin
    re-disparar análisis IA.
  - Distinto y cambio en objeto / CPV / importe → UPDATE + marcamos para
    re-análisis IA.

Esta función NO descarga pliegos ni llama a Claude — se limita a la tabla
`licitaciones` y deja todo listo para las siguientes etapas.
"""
from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone

from src.filtros.criterios import evaluar
from src.modelos import Licitacion

log = logging.getLogger(__name__)


@dataclass
class PersistResult:
    estado: str  # "nueva" | "actualizada_relevante" | "actualizada_menor" | "sin_cambios"
    pasa_filtros: bool
    motivo_descarte: str | None
    requiere_reanalisis_ia: bool


# --- Serialización ----------------------------------------------------------
def _to_row(licit: Licitacion) -> dict:
    return {
        "uuid_placsp": licit.uuid_placsp,
        "expediente": licit.expediente,
        "enlace_placsp": licit.enlace_placsp,
        "organo_contratacion": licit.organo_contratacion,
        "organo_nif": licit.organo_nif,
        "objeto": licit.objeto,
        "cpv_principal": licit.cpv_principal,
        "cpvs_secundarios": json.dumps(licit.cpvs_secundarios, ensure_ascii=False)
            if licit.cpvs_secundarios else None,
        "importe_sin_iva": licit.importe_sin_iva,
        "importe_con_iva": licit.importe_con_iva,
        "moneda": licit.moneda,
        "provincia": licit.provincia,
        "municipio": licit.municipio,
        "tipo_contrato": licit.tipo_contrato,
        "procedimiento": licit.procedimiento,
        "fecha_publicacion": licit.fecha_publicacion,
        "fecha_limite_presentacion": licit.fecha_limite_presentacion,
        "estado": licit.estado,
        "duracion_contrato": licit.duracion_contrato,
        "lugar_ejecucion": licit.lugar_ejecucion,
        "documentos_urls": json.dumps(
            [{"tipo": d.tipo, "url": d.url, "nombre": d.nombre} for d in licit.documentos],
            ensure_ascii=False,
        ) if licit.documentos else None,
        "hash_contenido": licit.hash_contenido,
    }


# --- Lógica de cambio relevante --------------------------------------------
_CAMPOS_RELEVANTES = ("objeto", "cpv_principal", "importe_sin_iva")


def _cambio_relevante(existente: sqlite3.Row, nuevo: dict) -> bool:
    for campo in _CAMPOS_RELEVANTES:
        if existente[campo] != nuevo[campo]:
            return True
    return False


# --- Upsert ----------------------------------------------------------------
def upsert_licitacion(conn: sqlite3.Connection, licit: Licitacion) -> PersistResult:
    fila = conn.execute(
        "SELECT * FROM licitaciones WHERE uuid_placsp = ?", (licit.uuid_placsp,)
    ).fetchone()

    pasa, motivo = evaluar(licit)
    row = _to_row(licit)
    ahora = datetime.now(timezone.utc).isoformat(timespec="seconds")

    if fila is None:
        # INSERT nuevo
        columnas = list(row.keys()) + [
            "pasa_filtros", "motivo_descarte", "fecha_ingesta", "fecha_ultima_actualizacion"
        ]
        valores = list(row.values()) + [
            1 if pasa else 0, motivo, ahora, ahora,
        ]
        placeholders = ",".join("?" * len(columnas))
        conn.execute(
            f"INSERT INTO licitaciones ({','.join(columnas)}) VALUES ({placeholders})",
            valores,
        )
        return PersistResult(
            estado="nueva",
            pasa_filtros=pasa,
            motivo_descarte=motivo,
            requiere_reanalisis_ia=pasa,
        )

    # Existe ya: comparar hash
    if fila["hash_contenido"] == licit.hash_contenido:
        # Sin cambios materiales: sólo refrescamos timestamp
        conn.execute(
            "UPDATE licitaciones SET fecha_ultima_actualizacion = ? WHERE id = ?",
            (ahora, fila["id"]),
        )
        return PersistResult(
            estado="sin_cambios",
            pasa_filtros=bool(fila["pasa_filtros"]),
            motivo_descarte=fila["motivo_descarte"],
            requiere_reanalisis_ia=False,
        )

    # Cambio: actualizar
    relevante = _cambio_relevante(fila, row)
    set_clause = ",".join(f"{k} = ?" for k in row.keys()) + (
        ", pasa_filtros = ?, motivo_descarte = ?, fecha_ultima_actualizacion = ?"
    )
    valores = list(row.values()) + [1 if pasa else 0, motivo, ahora, fila["id"]]
    conn.execute(f"UPDATE licitaciones SET {set_clause} WHERE id = ?", valores)
    return PersistResult(
        estado="actualizada_relevante" if relevante else "actualizada_menor",
        pasa_filtros=pasa,
        motivo_descarte=motivo,
        requiere_reanalisis_ia=relevante and pasa,
    )


# --- Reaplicar filtros sin reingesta ----------------------------------------
def reaplicar_filtros(conn: sqlite3.Connection) -> dict[str, int]:
    """Recorre todas las licitaciones y recalcula `pasa_filtros` y motivo.

    Útil cuando se amplían los CPVs o se cambian los umbrales: no requiere
    volver a descargar ni parsear, sólo recorre la tabla.

    Devuelve diccionario con conteos {antes_pasaban, ahora_pasan, ...}.
    """
    filas = conn.execute(
        "SELECT id, uuid_placsp, expediente, objeto, cpv_principal, "
        "importe_sin_iva, importe_con_iva, provincia, fecha_limite_presentacion, "
        "pasa_filtros FROM licitaciones"
    ).fetchall()

    stats = {"total": 0, "antes_pasaban": 0, "ahora_pasan": 0, "cambiaron": 0}
    for f in filas:
        stats["total"] += 1
        if f["pasa_filtros"]:
            stats["antes_pasaban"] += 1
        licit_min = Licitacion(
            uuid_placsp=f["uuid_placsp"],
            expediente=f["expediente"],
            objeto=f["objeto"],
            cpv_principal=f["cpv_principal"],
            importe_sin_iva=f["importe_sin_iva"],
            importe_con_iva=f["importe_con_iva"],
            provincia=f["provincia"],
            fecha_limite_presentacion=f["fecha_limite_presentacion"],
        )
        pasa, motivo = evaluar(licit_min)
        if pasa:
            stats["ahora_pasan"] += 1
        if bool(f["pasa_filtros"]) != pasa:
            stats["cambiaron"] += 1
            conn.execute(
                "UPDATE licitaciones SET pasa_filtros = ?, motivo_descarte = ? WHERE id = ?",
                (1 if pasa else 0, motivo, f["id"]),
            )
    return stats
