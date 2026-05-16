"""Modelos de datos del dominio (paralelos al esquema SQLite)."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Documento:
    """Documento publicado junto a una licitación (pliego, anexo, etc.)."""
    url: str
    tipo: str | None = None        # PCAP, PPT, anexo, otros
    nombre: str | None = None


@dataclass
class Licitacion:
    """Datos de una licitación extraídos del XML CODICE 2.07 de PLACSP."""
    uuid_placsp: str
    expediente: str
    enlace_placsp: str | None = None
    organo_contratacion: str | None = None
    organo_nif: str | None = None
    objeto: str | None = None
    cpv_principal: str | None = None
    cpvs_secundarios: list[str] = field(default_factory=list)
    importe_sin_iva: float | None = None
    importe_con_iva: float | None = None
    moneda: str = "EUR"
    provincia: str | None = None
    municipio: str | None = None
    tipo_contrato: str | None = None
    procedimiento: str | None = None
    fecha_publicacion: str | None = None
    fecha_limite_presentacion: str | None = None
    estado: str | None = None
    duracion_contrato: str | None = None
    lugar_ejecucion: str | None = None
    documentos: list[Documento] = field(default_factory=list)
    hash_contenido: str | None = None


@dataclass
class ResultadoAnalisisIA:
    """JSON que devuelve Claude tras analizar los pliegos."""
    encaje: str                                  # alto | medio | bajo | ninguno
    encaje_score: int                            # 0-100
    motivo: str
    resumen: str
    productos_aplicables: list[str] = field(default_factory=list)
    requisitos_criticos: list[str] = field(default_factory=list)
    banderas_rojas: list[str] = field(default_factory=list)
