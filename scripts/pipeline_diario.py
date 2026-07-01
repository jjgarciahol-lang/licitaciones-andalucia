"""Pipeline diario sin análisis IA.

Este es el entrypoint que dispara el Task Scheduler de Windows cada mañana.
NO llama a la API de Anthropic. El análisis IA se hace después manualmente con
Claude Code (modo Max).

Flujo:
  1. Cerrar ejecuciones huérfanas previas
  2. Descargar el ZIP del mes en curso (force=True, lo regenera PLACSP a diario)
  3. Parsear el ZIP completo y upsert en la tabla `licitaciones`
  4. Reaplicar filtros sobre toda la DB (por si cambiaron CPVs, importes, etc.)
  5. Descargar pliegos de candidatas vivas que aún no tienen
  6. Extraer texto a .txt para que el análisis manual sea rápido
  7. Regenerar dashboard/index.html
  8. Registrar resumen en log_ejecuciones

Uso:
  python scripts/pipeline_diario.py
  python scripts/pipeline_diario.py --omitir-descarga    # útil para tests
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import traceback
from collections import Counter
from datetime import date, datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Reduce ruido de descargas con URLs cifradas largas
logging.getLogger("src.pliegos.descargador").setLevel(logging.ERROR)

from src.db import conexion  # noqa: E402
from src.db_utils import cerrar_ejecuciones_huerfanas  # noqa: E402
from src.ingesta.codice_parser import parsear_zip  # noqa: E402
from src.ingesta.persistencia import reaplicar_filtros, upsert_licitacion  # noqa: E402
from src.ingesta.placsp_downloader import descargar_mes  # noqa: E402
from src.logging_setup import configurar_logging  # noqa: E402
from src.pliegos.descargador import descargar_pliegos_de_licitacion  # noqa: E402
from src.pliegos.extractor import extraer_y_persistir  # noqa: E402


def _abrir_log(conn) -> int:
    cur = conn.execute(
        """INSERT INTO log_ejecuciones (etapa, estado, metadata)
           VALUES ('pipeline_diario', 'en_curso', ?)""",
        (json.dumps({"inicio": datetime.now(timezone.utc).isoformat()}),),
    )
    return cur.lastrowid


def _cerrar_log(conn, log_id: int, estado: str, **kw) -> None:
    ahora = datetime.now(timezone.utc).isoformat(timespec="seconds")
    sets = ["fecha_fin = ?", "estado = ?"]
    valores: list = [ahora, estado]
    for campo in (
        "licitaciones_nuevas", "licitaciones_actualizadas",
        "licitaciones_pasan_filtros", "pliegos_descargados",
        "error_mensaje", "error_traceback",
    ):
        if campo in kw:
            sets.append(f"{campo} = ?")
            valores.append(kw[campo])
    if "metadata" in kw:
        sets.append("metadata = ?")
        valores.append(json.dumps(kw["metadata"], ensure_ascii=False))
    valores.append(log_id)
    conn.execute(f"UPDATE log_ejecuciones SET {','.join(sets)} WHERE id = ?", valores)


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--omitir-descarga", action="store_true",
                   help="No bajar el ZIP — solo re-procesar lo que ya está")
    p.add_argument("--omitir-pliegos", action="store_true",
                   help="No bajar pliegos — sólo ingesta y dashboard")
    args = p.parse_args()

    log = configurar_logging("pipeline_diario")
    hoy = date.today()
    log.info("=" * 60)
    log.info("Pipeline diario — %s", hoy.isoformat())
    log.info("=" * 60)

    contadores: Counter = Counter()

    with conexion() as conn:
        # Paso 0: ejecuciones huérfanas
        abortadas = cerrar_ejecuciones_huerfanas(conn)
        if abortadas:
            log.info("Ejecuciones huérfanas marcadas como abortadas: %d", abortadas)

        log_id = _abrir_log(conn)
        try:
            # Paso 1: descargar ZIP del mes en curso
            if not args.omitir_descarga:
                log.info("[1/5] Descargando ZIP de PLACSP del mes en curso...")
                try:
                    ruta_zip = descargar_mes(hoy.year, hoy.month, force=True)
                except RuntimeError:
                    # A principios de mes PLACSP aún no ha publicado el ZIP del mes nuevo
                    mes_ant = hoy.month - 1 or 12
                    año_ant = hoy.year if hoy.month > 1 else hoy.year - 1
                    log.warning("ZIP %d-%02d no disponible, usando mes anterior %d-%02d",
                                hoy.year, hoy.month, año_ant, mes_ant)
                    ruta_zip = descargar_mes(año_ant, mes_ant, force=False)
            else:
                from src.ingesta.placsp_downloader import ruta_local
                ruta_zip = ruta_local(hoy.year, hoy.month)
                log.info("[1/5] Saltando descarga, uso %s", ruta_zip)

            # Paso 2: parsear + upsert
            log.info("[2/5] Parseando ZIP y haciendo upsert en DB...")
            for licit in parsear_zip(ruta_zip, solo_pub=True):
                res = upsert_licitacion(conn, licit)
                contadores[res.estado] += 1
            log.info("    nuevas=%d, actualizadas_rel=%d, actualizadas_men=%d, sin_cambios=%d",
                     contadores["nueva"], contadores["actualizada_relevante"],
                     contadores["actualizada_menor"], contadores["sin_cambios"])

            # Paso 3: reaplicar filtros sobre TODA la DB
            log.info("[3/5] Reaplicando filtros sobre toda la DB...")
            stats_filtros = reaplicar_filtros(conn)
            log.info("    total=%d, pasaban=%d, ahora_pasan=%d, cambiaron=%d",
                     stats_filtros["total"], stats_filtros["antes_pasaban"],
                     stats_filtros["ahora_pasan"], stats_filtros["cambiaron"])

            # Paso 4: descargar y extraer pliegos de candidatas vivas sin pliego
            pliegos_descargados = 0
            extracciones = 0
            if not args.omitir_pliegos:
                log.info("[4/5] Descargando pliegos de candidatas nuevas...")
                pendientes = conn.execute(
                    """SELECT id, expediente FROM licitaciones l
                       WHERE l.pasa_filtros = 1
                         AND l.fecha_limite_presentacion > datetime('now')
                         AND NOT EXISTS (
                           SELECT 1 FROM pliegos_descargados p
                           WHERE p.licitacion_id = l.id
                             AND p.ruta_local IS NOT NULL
                         )"""
                ).fetchall()
                log.info("    %d candidatas sin pliegos descargados todavía", len(pendientes))
                for f in pendientes:
                    resultados = descargar_pliegos_de_licitacion(conn, f["id"])
                    for r in resultados:
                        if r["estado"] in ("descargado", "ya_existia") and r.get("ruta_local"):
                            pliegos_descargados += 1
                            res = extraer_y_persistir(conn, f["id"], Path(r["ruta_local"]))
                            if res.ok:
                                extracciones += 1
                log.info("    pliegos descargados: %d · texto extraído: %d",
                         pliegos_descargados, extracciones)
            else:
                log.info("[4/5] Pliegos omitidos por --omitir-pliegos")

            # Paso 5: regenerar dashboard
            log.info("[5/5] Regenerando dashboard...")
            import scripts.generar_dashboard as gen  # type: ignore
            gen.main()

            pasan_filtros = stats_filtros["ahora_pasan"]
            _cerrar_log(
                conn, log_id, "ok",
                licitaciones_nuevas=contadores["nueva"],
                licitaciones_actualizadas=(
                    contadores["actualizada_relevante"] + contadores["actualizada_menor"]
                ),
                licitaciones_pasan_filtros=pasan_filtros,
                pliegos_descargados=pliegos_descargados,
                metadata={
                    "fecha": hoy.isoformat(),
                    "desglose_upsert": dict(contadores),
                    "stats_filtros": stats_filtros,
                    "extracciones": extracciones,
                },
            )
            log.info("Pipeline completado OK · candidatas vivas: %d", pasan_filtros)

        except Exception as e:
            tb = traceback.format_exc()
            log.exception("Pipeline FALLÓ: %s", e)
            _cerrar_log(conn, log_id, "error",
                        error_mensaje=str(e), error_traceback=tb,
                        metadata={"fecha": hoy.isoformat()})
            return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
