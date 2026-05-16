"""Modelos de datos del subproyecto 'mapa' (paralelos al esquema SQLite)."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ClientePotencial:
    """Un punto en el mapa: colegio, ayuntamiento, guardería u hotel familiar."""
    id: str                          # hash estable de (fuente, codigo_origen)
    tipo: str                        # ver TIPOS_CLIENTE
    nombre: str
    provincia: str
    fuente: str                      # ver FUENTES

    direccion: str | None = None
    municipio: str | None = None
    cp: str | None = None
    telefono: str | None = None
    email: str | None = None
    web: str | None = None
    lat: float | None = None
    lon: float | None = None
    etapas: str | None = None        # solo colegios — CSV "EI,EP,ESO,BACH,FP"
    codigo_origen: str | None = None # ID en la fuente (código de centro, INE, OSM id, etc.)
    confianza: str = "alta"          # alta | media | baja (ej: hoteles OSM = baja)
