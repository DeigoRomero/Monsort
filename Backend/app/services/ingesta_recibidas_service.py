r"""
Ingesta de metadata de CFDI RECIBIDOS (Monsort como receptor).

Se separa de ingerir_metadata() / ingerir_cfdi() porque hace algo
distinto: para las EMITIDAS la metadata solo confirma que el comprobante
existe (el dato real viene del XML o de Gmail), mientras que para las
RECIBIDAS la metadata ES el dato. No hay XML que parsear.

PROPIEDAD DE COLUMNAS

    Hechos de emision   folio_fiscal, rfc_emisor, nombre_emisor,
                        rfc_receptor, fecha_emision, monto_total,
                        efecto_comprobante, rfc_pac
                        -> los escribe la ingesta la primera vez y
                           NUNCA los vuelve a pisar.

    Hechos de estatus   sat_estado, fecha_cancelacion
                        -> los escribe tanto la ingesta como la
                           verificacion SOAP. SIEMPRE se actualizan.

Por eso el UPSERT no es on_conflict_do_nothing sino do_update con el
set_ acotado a las columnas de estatus: el barrido diario vuelve a
descargar rangos ya ingeridos, y si la fila se saltara entera las
cancelaciones nuevas nunca entrarian.

Ubicacion sugerida: app/services/ingesta_recibidas_service.py
"""

from __future__ import annotations

import logging

from sqlalchemy import literal_column
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session
from sqlalchemy.sql import func

from app.services.parser_metadata import parsear_metadata

logger = logging.getLogger(__name__)

# Filas por sentencia. Un paquete del SAT trae hasta 10,000 comprobantes;
# mandarlos en una sola sentencia hace un statement enorme y dificil de
# diagnosticar cuando algo falla.
TAMANIO_LOTE = 500

# Columnas que la ingesta SI puede sobreescribir en una fila existente.
# Todo lo demas son hechos de emision y queda intacto.
COLUMNAS_ESTATUS = ("sat_estado", "fecha_cancelacion")

# Cuantas lineas rechazadas se guardan en error_ingesta. El campo es Text,
# pero no tiene sentido volcar miles de lineas ahi.
MAX_RECHAZOS_REPORTADOS = 20


def _en_lotes(secuencia, tamanio):
    for inicio in range(0, len(secuencia), tamanio):
        yield secuencia[inicio:inicio + tamanio]


def _resumen_rechazos(rechazadas: list[dict]) -> str:
    """Texto compacto para SolicitudesSAT.error_ingesta."""
    if not rechazadas:
        return ""

    from collections import Counter
    # Se agrupa por motivo: 500 filas con el mismo problema son un
    # problema, no 500.
    conteo = Counter(r["motivo"].split(":")[0] for r in rechazadas)
    lineas = [f"{len(rechazadas)} fila(s) rechazada(s):"]
    lineas += [f"  {motivo} x{n}" for motivo, n in conteo.most_common()]

    lineas.append("Ejemplos:")
    for r in rechazadas[:MAX_RECHAZOS_REPORTADOS]:
        lineas.append(f"  [{r['archivo']}] {r['motivo']}")

    return "\n".join(lineas)


def ingerir_metadata_recibidas(
    db: Session,
    contenido_zip: bytes,
    id_solicitud: int | None = None,
) -> tuple[int, int, str]:
    """
    Parsea el ZIP de metadata e inserta/actualiza FacturasRecibidas.

    Devuelve (nuevas, actualizadas, texto_rechazos).

    nuevas       filas que no existian
    actualizadas filas que ya existian; se les refresco el estatus
    rechazos     resumen para SolicitudesSAT.error_ingesta ("" si ninguno)

    Idempotente: correr dos veces el mismo paquete deja la base igual.
    """
    from app.modelos.facturas_recibidas import FacturasRecibidas

    validas, rechazadas = parsear_metadata(contenido_zip)

    if rechazadas:
        logger.warning(
            "Metadata de recibidas: %d fila(s) rechazada(s). Primera: %s",
            len(rechazadas), rechazadas[0]["motivo"],
        )

    if not validas:
        return 0, 0, _resumen_rechazos(rechazadas)

    # El parser ya devuelve los nombres de columna de la tabla; solo se
    # agrega lo que el parser no puede saber.
    filas = []
    for fila in validas:
        registro = dict(fila)
        registro["origen"] = "sat"
        registro["id_solicitud"] = id_solicitud
        filas.append(registro)

    nuevas = 0
    actualizadas = 0

    for lote in _en_lotes(filas, TAMANIO_LOTE):
        sentencia = insert(FacturasRecibidas).values(lote)

        # Solo las columnas de estatus. rfc_emisor, monto_total, etc. se
        # quedan como entraron la primera vez.
        campos = {
            columna: getattr(sentencia.excluded, columna)
            for columna in COLUMNAS_ESTATUS
        }
        # onupdate= del modelo no dispara en un INSERT ... ON CONFLICT,
        # hay que ponerlo a mano.
        campos["actualizado_en"] = func.now()

        sentencia = sentencia.on_conflict_do_update(
            index_elements=["folio_fiscal"],
            set_=campos,
        ).returning(
            # xmax = 0 distingue INSERT de UPDATE en la misma sentencia.
            literal_column("(xmax = 0)").label("es_insert")
        )

        for (es_insert,) in db.execute(sentencia):
            if es_insert:
                nuevas += 1
            else:
                actualizadas += 1

    logger.info(
        "Recibidas ingeridas: %d nuevas, %d actualizadas, %d rechazadas",
        nuevas, actualizadas, len(rechazadas),
    )

    return nuevas, actualizadas, _resumen_rechazos(rechazadas)