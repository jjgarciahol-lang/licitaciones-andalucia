"""Configuración de logging: salida a consola y a fichero diario en logs/."""
from __future__ import annotations

import logging
import sys
from datetime import date
from logging.handlers import RotatingFileHandler

from src.config import LOG_DIR, LOG_LEVEL

_FORMAT = "%(asctime)s [%(levelname)-7s] %(name)s: %(message)s"
_DATEFMT = "%Y-%m-%d %H:%M:%S"


def configurar_logging(nombre_ejecucion: str | None = None) -> logging.Logger:
    """Configura el logger raíz una sola vez. Devuelve un logger nombrado."""
    root = logging.getLogger()
    if getattr(root, "_licitaciones_configurado", False):
        return logging.getLogger(nombre_ejecucion or "licitaciones")

    root.setLevel(LOG_LEVEL)

    formatter = logging.Formatter(_FORMAT, datefmt=_DATEFMT)

    consola = logging.StreamHandler(sys.stdout)
    consola.setFormatter(formatter)
    root.addHandler(consola)

    nombre_log = f"pipeline_{date.today().isoformat()}.log"
    fichero = RotatingFileHandler(
        LOG_DIR / nombre_log,
        maxBytes=10_000_000,
        backupCount=5,
        encoding="utf-8",
    )
    fichero.setFormatter(formatter)
    root.addHandler(fichero)

    root._licitaciones_configurado = True  # type: ignore[attr-defined]

    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("anthropic").setLevel(logging.INFO)
    logging.getLogger("httpx").setLevel(logging.WARNING)

    return logging.getLogger(nombre_ejecucion or "licitaciones")
