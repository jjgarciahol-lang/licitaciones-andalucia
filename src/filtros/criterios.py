"""Filtros duros que se aplican a cada licitación tras la ingesta.

Si una licitación NO pasa los filtros, no se descarga su pliego ni se envía a
Claude — sólo queda registrada en la DB con `pasa_filtros=0` y un motivo.

Reglas (V1):
1. Provincia: o bien es andaluza, o bien no se ha podido detectar (Claude la
   inferirá del pliego). Si la provincia está identificada y NO es andaluza,
   se descarta.
2. CPV principal: debe empezar por alguno de los prefijos en CPVS_RELEVANTES.
3. Importe sin IVA: entre IMPORTE_MIN e IMPORTE_MAX. Si no hay importe sin
   IVA, se usa el importe con IVA como aproximación.
4. Fecha límite de presentación: debe ser futura (> ahora). Para backfills, se
   marcan como descartadas las que ya cerraron — quedan en DB pero no se
   analizan.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import NamedTuple

from src.config import (
    IMPORTE_MAX,
    IMPORTE_MIN,
    es_cpv_relevante,
    es_provincia_permitida,
)
from src.modelos import Licitacion


class Resultado(NamedTuple):
    pasa: bool
    motivo: str | None  # None si pasa


def _parsear_fecha_iso(fecha: str | None) -> datetime | None:
    """Acepta 'YYYY-MM-DD' o 'YYYY-MM-DDTHH:MM:SS' (con o sin zona)."""
    if not fecha:
        return None
    try:
        # datetime.fromisoformat acepta ambos en Python 3.11+
        dt = datetime.fromisoformat(fecha)
        if dt.tzinfo is None:
            # Asumimos hora local española; para comparar usamos UTC
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        return None


def evaluar(licit: Licitacion, *, ahora: datetime | None = None) -> Resultado:
    """Devuelve (pasa, motivo_descarte)."""
    ahora = ahora or datetime.now(timezone.utc)

    # 1. Provincia
    if licit.provincia is not None and not es_provincia_permitida(licit.provincia):
        return Resultado(False, f"fuera de Andalucía ({licit.provincia})")

    # 2. CPV
    if not es_cpv_relevante(licit.cpv_principal):
        return Resultado(False, f"CPV no relevante ({licit.cpv_principal})")

    # 3. Importe
    importe = licit.importe_sin_iva if licit.importe_sin_iva is not None else licit.importe_con_iva
    if importe is None:
        return Resultado(False, "sin importe")
    if importe < IMPORTE_MIN:
        return Resultado(False, f"importe demasiado bajo ({importe:.0f} €)")
    if importe > IMPORTE_MAX:
        return Resultado(False, f"importe demasiado alto ({importe:.0f} €)")

    # 4. Fecha límite presentación
    fecha_limite = _parsear_fecha_iso(licit.fecha_limite_presentacion)
    if fecha_limite is None:
        return Resultado(False, "sin fecha límite")
    if fecha_limite < ahora:
        return Resultado(False, "fecha límite pasada")

    return Resultado(True, None)
