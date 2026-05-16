"""Parser de los XML CODICE 2.07 que vienen dentro de los ZIPs de PLACSP.

Cada ZIP contiene varios ficheros .atom (uno por entrega diaria, paginados).
Cada .atom es un feed Atom con muchos `<entry>`, y cada entry contiene un
`<cac-place-ext:ContractFolderStatus>` con todos los datos de la licitación.

Esta es la fuente de la verdad para los XPath. Si PLACSP cambia el esquema,
hay que revisar `NS` y los `_TAG_*`.
"""
from __future__ import annotations

import hashlib
import logging
import re
import unicodedata
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Iterator

from lxml import etree

from src.modelos import Documento, Licitacion

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Namespaces de los XML de PLACSP
# ---------------------------------------------------------------------------
NS = {
    "atom": "http://www.w3.org/2005/Atom",
    "cbc": "urn:dgpe:names:draft:codice:schema:xsd:CommonBasicComponents-2",
    "cac": "urn:dgpe:names:draft:codice:schema:xsd:CommonAggregateComponents-2",
    "cac-place-ext": "urn:dgpe:names:draft:codice-place-ext:schema:xsd:CommonAggregateComponents-2",
    "cbc-place-ext": "urn:dgpe:names:draft:codice-place-ext:schema:xsd:CommonBasicComponents-2",
    "at": "http://purl.org/atompub/tombstones/1.0",
}

# ---------------------------------------------------------------------------
# Tablas de códigos
# ---------------------------------------------------------------------------
# NUTS-2021 — sólo provincias de Andalucía (el resto se descartan por filtro de
# provincias andaluzas; cualquier código NUTS español fuera de ES61* implica
# provincia distinta y no nos interesa para V1).
NUTS_ANDALUCIA = {
    "ES611": "Almería",
    "ES612": "Cádiz",
    "ES613": "Córdoba",
    "ES614": "Granada",
    "ES615": "Huelva",
    "ES616": "Jaén",
    "ES617": "Málaga",
    "ES618": "Sevilla",
}

# Lista de nombres de provincia (todas las españolas) para el fallback por
# jerarquía ParentLocatedParty / texto plano. La comparación es normalizada.
PROVINCIAS_ES = {
    "alava", "albacete", "alicante", "almeria", "asturias", "avila", "badajoz",
    "barcelona", "burgos", "caceres", "cadiz", "cantabria", "castellon",
    "ciudad real", "cordoba", "cuenca", "girona", "granada", "guadalajara",
    "guipuzcoa", "huelva", "huesca", "illes balears", "jaen", "la coruna",
    "la rioja", "las palmas", "leon", "lleida", "lugo", "madrid", "malaga",
    "murcia", "navarra", "ourense", "palencia", "pontevedra", "salamanca",
    "santa cruz de tenerife", "segovia", "sevilla", "soria", "tarragona",
    "teruel", "toledo", "valencia", "valladolid", "vizcaya", "zamora",
    "zaragoza", "ceuta", "melilla",
}

# Mapeo normalizado -> nombre canónico con tildes
_PROVINCIA_CANONICA = {
    "almeria": "Almería", "cadiz": "Cádiz", "cordoba": "Córdoba",
    "granada": "Granada", "huelva": "Huelva", "jaen": "Jaén",
    "malaga": "Málaga", "sevilla": "Sevilla", "leon": "León",
    "caceres": "Cáceres", "avila": "Ávila", "castellon": "Castellón",
    "la coruna": "A Coruña", "guipuzcoa": "Gipuzkoa", "vizcaya": "Bizkaia",
    "alava": "Araba/Álava", "jaen": "Jaén",
}

# Tipos de contrato (cbc:TypeCode en ProcurementProject)
TIPO_CONTRATO = {
    "1": "Suministros",
    "2": "Servicios",
    "3": "Obras",
    "21": "Privados de la Administración",
    "22": "Patrimoniales",
    "31": "Concesión de obras",
    "32": "Concesión de servicios",
    "40": "Administrativos especiales",
    "50": "Otros",
}

# Procedimientos (cbc:ProcedureCode)
PROCEDIMIENTO = {
    "1": "Abierto",
    "2": "Restringido",
    "3": "Negociado sin publicidad",
    "4": "Negociado con publicidad",
    "5": "Diálogo competitivo",
    "7": "Contrato menor",
    "8": "Derivado de acuerdo marco",
    "9": "Abierto simplificado",
    "10": "Asociación para la innovación",
    "11": "Licitación con negociación",
    "12": "Concurso de proyectos",
    "13": "Procedimiento especial obras de la concesión",
    "999": "Otros",
}


def _norm(texto: str | None) -> str:
    """Quita tildes, minúsculas y strip."""
    if not texto:
        return ""
    sin_tildes = unicodedata.normalize("NFKD", texto)
    sin_tildes = "".join(c for c in sin_tildes if not unicodedata.combining(c))
    return sin_tildes.strip().lower()


def _texto(elem: etree._Element | None, xpath: str) -> str | None:
    """Devuelve texto de un subnodo o None si no existe / está vacío."""
    if elem is None:
        return None
    sub = elem.find(xpath, NS)
    if sub is None or sub.text is None:
        return None
    txt = sub.text.strip()
    return txt or None


def _float(valor: str | None) -> float | None:
    if valor is None:
        return None
    try:
        return float(valor.replace(",", "."))
    except (ValueError, AttributeError):
        return None


# ---------------------------------------------------------------------------
# Extracción de campos concretos
# ---------------------------------------------------------------------------
def _extraer_uuid(entry: etree._Element) -> str | None:
    """`<id>` del entry. Es una URL acabada en el id numérico de la licitación."""
    return _texto(entry, "atom:id")


def _extraer_enlace(entry: etree._Element) -> str | None:
    link = entry.find("atom:link", NS)
    return link.get("href") if link is not None else None


def _extraer_organo(party: etree._Element | None) -> tuple[str | None, str | None]:
    if party is None:
        return None, None
    nombre = _texto(party, "cac:PartyName/cbc:Name")
    nif = None
    for ident in party.findall("cac:PartyIdentification/cbc:ID", NS):
        if ident.get("schemeName") == "NIF" and ident.text:
            nif = ident.text.strip()
            break
    return nombre, nif


def _extraer_provincia_municipio(
    cfs: etree._Element,
    party: etree._Element | None,
    located_contracting_party: etree._Element | None,
) -> tuple[str | None, str | None]:
    """Intenta extraer (provincia, municipio) usando varios caminos en cascada:

    1. RealizedLocation > CountrySubentityCode (NUTS) — el más fiable
    2. RealizedLocation > CountrySubentity (texto plano)
    3. ParentLocatedParty recursivo en LocatedContractingParty
    4. PostalAddress > CityName del órgano (sólo municipio)
    """
    municipio = None
    provincia = None

    realized = cfs.find("cac:ProcurementProject/cac:RealizedLocation", NS)
    if realized is not None:
        nuts = _texto(realized, "cbc:CountrySubentityCode")
        if nuts and nuts in NUTS_ANDALUCIA:
            provincia = NUTS_ANDALUCIA[nuts]
        elif nuts and nuts.startswith("ES") and len(nuts) == 5:
            # Provincia española fuera de Andalucía: la dejamos como tal para
            # que los filtros la descarten claramente.
            provincia = nuts  # luego no pasará el filtro andaluz
        else:
            txt = _texto(realized, "cbc:CountrySubentity")
            if txt:
                provincia = _PROVINCIA_CANONICA.get(_norm(txt), txt)

        municipio = _texto(realized, "cac:Address/cbc:CityName")

    # Fallback: jerarquía ParentLocatedParty
    if provincia is None and located_contracting_party is not None:
        provincia = _provincia_desde_jerarquia(located_contracting_party)

    # Municipio del órgano si no hay del RealizedLocation
    if municipio is None and party is not None:
        municipio = _texto(party, "cac:PostalAddress/cbc:CityName")

    return provincia, municipio


def _provincia_desde_jerarquia(located: etree._Element) -> str | None:
    """Recorre cac-place-ext:ParentLocatedParty buscando nombres de provincia."""
    for nombre_elem in located.iter("{%s}Name" % NS["cbc"]):
        if nombre_elem.text is None:
            continue
        clave = _norm(nombre_elem.text)
        if clave in PROVINCIAS_ES:
            return _PROVINCIA_CANONICA.get(clave, nombre_elem.text.strip())
    return None


def _extraer_importes(pp: etree._Element | None) -> tuple[float | None, float | None, str]:
    if pp is None:
        return None, None, "EUR"
    budget = pp.find("cac:BudgetAmount", NS)
    if budget is None:
        return None, None, "EUR"
    sin_iva = _float(_texto(budget, "cbc:TaxExclusiveAmount"))
    con_iva = _float(_texto(budget, "cbc:TotalAmount"))
    # Moneda: cogemos del primer importe disponible
    moneda = "EUR"
    for tag in ("cbc:TaxExclusiveAmount", "cbc:TotalAmount", "cbc:EstimatedOverallContractAmount"):
        nodo = budget.find(tag, NS)
        if nodo is not None and nodo.get("currencyID"):
            moneda = nodo.get("currencyID")
            break
    return sin_iva, con_iva, moneda


def _extraer_cpvs(pp: etree._Element | None) -> tuple[str | None, list[str]]:
    if pp is None:
        return None, []
    codigos = [
        c.text.strip()
        for c in pp.findall("cac:RequiredCommodityClassification/cbc:ItemClassificationCode", NS)
        if c.text
    ]
    if not codigos:
        return None, []
    return codigos[0], codigos[1:]


def _extraer_fecha_limite(tp: etree._Element | None) -> str | None:
    if tp is None:
        return None
    deadline = tp.find("cac:TenderSubmissionDeadlinePeriod", NS)
    if deadline is None:
        return None
    fecha = _texto(deadline, "cbc:EndDate")
    hora = _texto(deadline, "cbc:EndTime")
    if not fecha:
        return None
    return f"{fecha}T{hora}" if hora else fecha


def _extraer_duracion(pp: etree._Element | None) -> str | None:
    if pp is None:
        return None
    planned = pp.find("cac:PlannedPeriod", NS)
    if planned is None:
        return None
    medida = planned.find("cbc:DurationMeasure", NS)
    if medida is not None and medida.text:
        unidad = medida.get("unitCode", "")
        return f"{medida.text} {unidad}".strip()
    inicio = _texto(planned, "cbc:StartDate")
    fin = _texto(planned, "cbc:EndDate")
    if inicio and fin:
        return f"{inicio} a {fin}"
    return None


def _extraer_documentos(cfs: etree._Element) -> list[Documento]:
    docs: list[Documento] = []
    mapeo = (
        ("cac:LegalDocumentReference", "PCAP"),
        ("cac:TechnicalDocumentReference", "PPT"),
        ("cac:AdditionalDocumentReference", "anexo"),
    )
    for xpath, tipo in mapeo:
        for d in cfs.findall(xpath, NS):
            uri = _texto(d, "cac:Attachment/cac:ExternalReference/cbc:URI")
            nombre = _texto(d, "cbc:ID")
            if uri:
                docs.append(Documento(url=uri, tipo=tipo, nombre=nombre))
    return docs


def _extraer_lugar_ejecucion(cfs: etree._Element) -> str | None:
    realized = cfs.find("cac:ProcurementProject/cac:RealizedLocation", NS)
    if realized is None:
        return None
    partes = [
        _texto(realized, "cac:Address/cbc:CityName"),
        _texto(realized, "cbc:CountrySubentity"),
    ]
    return ", ".join(p for p in partes if p) or None


# ---------------------------------------------------------------------------
# Función principal: parsear un entry completo
# ---------------------------------------------------------------------------
def _parsear_entry(entry: etree._Element) -> Licitacion | None:
    """Convierte un `<entry>` Atom en una Licitacion. Devuelve None si no se
    puede extraer ni siquiera la clave (uuid + expediente)."""
    uuid_placsp = _extraer_uuid(entry)
    cfs = entry.find("cac-place-ext:ContractFolderStatus", NS)
    if cfs is None or not uuid_placsp:
        return None

    expediente = _texto(cfs, "cbc:ContractFolderID") or ""
    if not expediente:
        return None

    pp = cfs.find("cac:ProcurementProject", NS)
    tp = cfs.find("cac:TenderingProcess", NS)
    lcp = cfs.find("cac-place-ext:LocatedContractingParty", NS)
    party = lcp.find("cac:Party", NS) if lcp is not None else None

    organo, nif = _extraer_organo(party)
    provincia, municipio = _extraer_provincia_municipio(cfs, party, lcp)
    importe_sin_iva, importe_con_iva, moneda = _extraer_importes(pp)
    cpv_principal, cpvs_secundarios = _extraer_cpvs(pp)
    procedimiento_codigo = _texto(tp, "cbc:ProcedureCode") if tp is not None else None
    estado_codigo = _texto(cfs, "cbc-place-ext:ContractFolderStatusCode")

    documentos = _extraer_documentos(cfs)

    objeto = _texto(pp, "cbc:Name") if pp is not None else None
    if not objeto:
        objeto = _texto(entry, "atom:title")

    licit = Licitacion(
        uuid_placsp=uuid_placsp,
        expediente=expediente,
        enlace_placsp=_extraer_enlace(entry),
        organo_contratacion=organo,
        organo_nif=nif,
        objeto=objeto,
        cpv_principal=cpv_principal,
        cpvs_secundarios=cpvs_secundarios,
        importe_sin_iva=importe_sin_iva,
        importe_con_iva=importe_con_iva,
        moneda=moneda or "EUR",
        provincia=provincia,
        municipio=municipio,
        tipo_contrato=TIPO_CONTRATO.get(_texto(pp, "cbc:TypeCode") or "", _texto(pp, "cbc:TypeCode")),
        procedimiento=PROCEDIMIENTO.get(procedimiento_codigo or "", procedimiento_codigo),
        fecha_publicacion=_texto(entry, "atom:updated"),
        fecha_limite_presentacion=_extraer_fecha_limite(tp),
        estado=estado_codigo,
        duracion_contrato=_extraer_duracion(pp),
        lugar_ejecucion=_extraer_lugar_ejecucion(cfs),
        documentos=documentos,
    )

    # Hash de contenido para detectar cambios entre versiones del mismo UUID.
    contenido_hashable = (
        f"{licit.objeto}|{licit.cpv_principal}|{licit.importe_sin_iva}|"
        f"{licit.fecha_limite_presentacion}|{licit.estado}"
    )
    licit.hash_contenido = hashlib.sha256(contenido_hashable.encode("utf-8")).hexdigest()[:16]

    return licit


# ---------------------------------------------------------------------------
# Entrada al parser: ficheros sueltos y ZIP completo
# ---------------------------------------------------------------------------
def parsear_atom_bytes(contenido: bytes, *, solo_pub: bool = True) -> Iterator[Licitacion]:
    """Parsea un .atom y va emitiendo Licitaciones.

    `solo_pub=True` filtra estados distintos de PUB en este punto (más barato
    que dejar pasar todo al pipeline siguiente).
    """
    root = etree.fromstring(contenido)  # noqa: S320 — origen confiable
    for entry in root.findall("atom:entry", NS):
        try:
            licit = _parsear_entry(entry)
        except Exception as e:
            log.warning("Error parseando entry: %s", e)
            continue
        if licit is None:
            continue
        if solo_pub and (licit.estado or "").upper() != "PUB":
            continue
        yield licit


def parsear_zip(ruta_zip: Path, *, solo_pub: bool = True) -> Iterator[Licitacion]:
    """Itera todos los .atom de un ZIP y emite todas las licitaciones."""
    with zipfile.ZipFile(ruta_zip) as z:
        atom_files = [n for n in z.namelist() if n.endswith(".atom")]
        log.info("ZIP %s contiene %d ficheros .atom", ruta_zip.name, len(atom_files))
        for nombre in atom_files:
            try:
                datos = z.read(nombre)
            except Exception as e:
                log.warning("No pude leer %s del ZIP: %s", nombre, e)
                continue
            count = 0
            for licit in parsear_atom_bytes(datos, solo_pub=solo_pub):
                count += 1
                yield licit
            log.debug("  %s: %d licitaciones (estado PUB%s)",
                      nombre, count, "" if solo_pub else " o cualquiera")
