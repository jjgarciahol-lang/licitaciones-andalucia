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
class Adjudicacion:
    """Adjudicatario de una licitación (un `<cac:TenderResult>` del XML CODICE).

    Una licitación puede tener varias adjudicaciones (por lotes). El cruce con
    el mapa comercial usa `nif` como clave para detectar el mismo contratista
    ganando licitaciones distintas.
    """
    uuid_placsp: str               # FK lógica a licitaciones.uuid_placsp
    nif: str | None                # NIF / CIF español del adjudicatario
    razon_social: str | None
    importe_adjudicacion: float | None  # cbc:LowerTenderAmount (suele coincidir con el adjudicado)
    fecha_adjudicacion: str | None      # cbc:AwardDate (YYYY-MM-DD)
    result_code: str | None             # cbc:ResultCode CODICE (8=adjudicado, 9=formalizado, etc.)
    es_pyme: bool | None                # cbc:SMEAwardedIndicator
    pais: str | None                    # cbc:AwardedOwnerNationalityCode (ES, FR, ...)
    direccion: str | None               # PhysicalLocation > Address (texto agregado)
    ciudad: str | None
    provincia: str | None               # CountrySubentity / NUTS
    codigo_postal: str | None


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
