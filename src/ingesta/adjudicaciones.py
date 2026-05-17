"""Extractor de adjudicatarios desde los XML CODICE de PLACSP.

Este módulo es INDEPENDIENTE del flujo principal `codice_parser.py +
persistencia.py`: no filtra por estado PUB (las adjudicaciones viven en
estados ADJ/RES/etc.) y solo extrae los `<cac:TenderResult>`. La motivación
es el subproyecto mapa, que necesita listar contratistas locales que han
ganado obra municipal — el dataset perfecto para identificar empresas
privadas que SÍ compran a Higiofi.

Reutiliza:
- Namespaces (NS) y helpers de texto del parser principal.
- Conexión y esquema de la DB principal (`licitaciones.db`).

No reutiliza:
- El filtro `solo_pub` del parser (aquí queremos TODOS los estados).
- El modelo `Licitacion` (aquí solo nos interesan los TenderResult).
"""
from __future__ import annotations

import logging
import sqlite3
import zipfile
from pathlib import Path
from typing import Iterator

from lxml import etree

from src.ingesta.codice_parser import NS, NUTS_ANDALUCIA, _PROVINCIA_CANONICA, _norm, _texto, _float
from src.modelos import Adjudicacion

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Extracción de TenderResult dentro de un ContractFolderStatus
# ---------------------------------------------------------------------------
def _extraer_adjudicaciones_de_cfs(
    cfs: etree._Element, uuid_placsp: str
) -> list[Adjudicacion]:
    """Itera todos los `<cac:TenderResult>` y devuelve Adjudicaciones."""
    adjudicaciones: list[Adjudicacion] = []
    for tr in cfs.findall("cac:TenderResult", NS):
        winning = tr.find("cac:WinningParty", NS)
        if winning is None:
            # TenderResult sin adjudicatario (puede ser "desierto", "anulado", etc.)
            continue

        # --- Datos del WinningParty ---
        nif = None
        for ident in winning.findall("cac:PartyIdentification/cbc:ID", NS):
            if ident.get("schemeName") == "NIF" and ident.text:
                nif = ident.text.strip()
                break
        razon_social = _texto(winning, "cac:PartyName/cbc:Name")

        # Si no hay ni NIF ni razón social, no nos sirve
        if not nif and not razon_social:
            continue

        ciudad = _texto(winning, "cac:PhysicalLocation/cac:Address/cbc:CityName")
        cp = _texto(winning, "cac:PhysicalLocation/cac:Address/cbc:PostalZone")
        nuts = _texto(winning, "cac:PhysicalLocation/cbc:CountrySubentityCode")
        provincia = NUTS_ANDALUCIA.get(nuts) if nuts else None
        if provincia is None and nuts and nuts.startswith("ES") and len(nuts) == 5:
            # NUTS fuera de Andalucía: lo dejamos con el nombre canónico si lo conocemos
            # via _PROVINCIA_CANONICA, si no, el código tal cual.
            provincia = nuts

        # Dirección de calle (raramente viene rellena en el dataset PLACSP)
        direccion = _texto(winning, "cac:PhysicalLocation/cac:Address/cbc:StreetName")

        # --- Datos del propio TenderResult ---
        result_code = _texto(tr, "cbc:ResultCode")
        fecha_award = _texto(tr, "cbc:AwardDate")
        importe = _float(_texto(tr, "cbc:LowerTenderAmount"))
        if importe is None:
            # Si no hay LowerTenderAmount, intenta con AwardedTenderedProject
            importe = _float(_texto(
                tr, "cac:AwardedTenderedProject/cac:LegalMonetaryTotal/cbc:TaxExclusiveAmount"
            ))
        sme_str = _texto(tr, "cbc:SMEAwardedIndicator")
        es_pyme = (sme_str or "").lower() == "true" if sme_str is not None else None
        pais = _texto(tr, "cbc:AwardedOwnerNationalityCode")

        adjudicaciones.append(Adjudicacion(
            uuid_placsp=uuid_placsp,
            nif=nif,
            razon_social=razon_social,
            importe_adjudicacion=importe,
            fecha_adjudicacion=fecha_award,
            result_code=result_code,
            es_pyme=es_pyme,
            pais=pais,
            direccion=direccion,
            ciudad=ciudad,
            provincia=provincia,
            codigo_postal=cp,
        ))

    return adjudicaciones


# ---------------------------------------------------------------------------
# Recorridos de .atom y .zip — sin filtro de estado
# ---------------------------------------------------------------------------
def parsear_atom_bytes(contenido: bytes) -> Iterator[Adjudicacion]:
    """Itera todas las adjudicaciones de un .atom independientemente del estado."""
    root = etree.fromstring(contenido)  # noqa: S320
    for entry in root.findall("atom:entry", NS):
        try:
            uuid_placsp = _texto(entry, "atom:id")
            cfs = entry.find("cac-place-ext:ContractFolderStatus", NS)
            if cfs is None or not uuid_placsp:
                continue
            yield from _extraer_adjudicaciones_de_cfs(cfs, uuid_placsp)
        except Exception as e:
            log.warning("Error extrayendo TenderResult: %s", e)


def parsear_zip(ruta_zip: Path) -> Iterator[Adjudicacion]:
    """Itera todos los .atom de un ZIP y emite todas las Adjudicaciones."""
    with zipfile.ZipFile(ruta_zip) as z:
        atom_files = [n for n in z.namelist() if n.endswith(".atom")]
        log.info("ZIP %s contiene %d .atom — extrayendo adjudicaciones",
                 ruta_zip.name, len(atom_files))
        for nombre in atom_files:
            try:
                datos = z.read(nombre)
            except Exception as e:
                log.warning("No pude leer %s del ZIP: %s", nombre, e)
                continue
            yield from parsear_atom_bytes(datos)


# ---------------------------------------------------------------------------
# Upsert en la tabla `adjudicaciones`
# ---------------------------------------------------------------------------
def upsert_adjudicacion(conn: sqlite3.Connection, adj: Adjudicacion) -> str:
    """Inserta o actualiza una adjudicación (clave: uuid_placsp + nif).

    Devuelve 'nueva' o 'actualizada'.
    """
    existente = conn.execute(
        "SELECT id FROM adjudicaciones WHERE uuid_placsp = ? AND nif IS ?",
        (adj.uuid_placsp, adj.nif),
    ).fetchone()

    if existente:
        conn.execute(
            """
            UPDATE adjudicaciones SET
                razon_social=?, importe_adjudicacion=?, fecha_adjudicacion=?,
                result_code=?, es_pyme=?, pais=?, direccion=?, ciudad=?,
                provincia=?, codigo_postal=?
            WHERE id=?
            """,
            (
                adj.razon_social, adj.importe_adjudicacion, adj.fecha_adjudicacion,
                adj.result_code, int(adj.es_pyme) if adj.es_pyme is not None else None,
                adj.pais, adj.direccion, adj.ciudad, adj.provincia, adj.codigo_postal,
                existente["id"],
            ),
        )
        return "actualizada"

    conn.execute(
        """
        INSERT INTO adjudicaciones (
            uuid_placsp, nif, razon_social, importe_adjudicacion, fecha_adjudicacion,
            result_code, es_pyme, pais, direccion, ciudad, provincia, codigo_postal
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            adj.uuid_placsp, adj.nif, adj.razon_social, adj.importe_adjudicacion,
            adj.fecha_adjudicacion, adj.result_code,
            int(adj.es_pyme) if adj.es_pyme is not None else None,
            adj.pais, adj.direccion, adj.ciudad, adj.provincia, adj.codigo_postal,
        ),
    )
    return "nueva"
