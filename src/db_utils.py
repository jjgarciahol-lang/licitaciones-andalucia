"""Utilidades sobre la DB que no encajan en `src/db.py` (que es solo schema)."""
from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timedelta, timezone

log = logging.getLogger(__name__)


def cerrar_ejecuciones_huerfanas(conn: sqlite3.Connection, *, antiguedad_horas: int = 1) -> int:
    """Marca como 'abortada' las ejecuciones que llevan demasiado en 'en_curso'.

    Esto pasa cuando se mata el proceso (Ctrl+C, kill) durante un backfill.
    Llamar al arrancar cualquier script de pipeline para no acumular basura.

    Devuelve el número de filas afectadas.
    """
    limite = (datetime.now(timezone.utc) - timedelta(hours=antiguedad_horas)).isoformat(timespec="seconds")
    cur = conn.execute(
        """UPDATE log_ejecuciones
           SET estado = 'abortada',
               fecha_fin = datetime('now'),
               error_mensaje = COALESCE(error_mensaje, 'Marcada abortada automáticamente: proceso quedó colgado')
           WHERE estado = 'en_curso' AND fecha_inicio < ?""",
        (limite,),
    )
    if cur.rowcount > 0:
        log.warning("%d ejecucion(es) en 'en_curso' marcadas como abortadas", cur.rowcount)
    return cur.rowcount
