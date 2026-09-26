r"""
Ingesta de metadata de CFDI EMITIDOS (Monsort como emisor) y reconciliacion
contra lo que si esta en `Facturas`.

Antes, `ingerir_metadata()` en descarga_masiva_service.py contaba los UUID del
paquete contra `Facturas` y tiraba el archivo. Su propio comentario lo decia:
"de momento solo se contabiliza y se registra". El problema practico apareció
el 26/09/2026: habia 10 pagos (CP) apuntando a facturas ausentes de la base y
no habia forma de saber de que mes eran, porque un UUID no lleva fecha. Ese
dato venia en la FechaEmision del TXT que se descartaba en cada corrida.

EL ORDEN QUE ESTO HABILITA

    metadata  ->  indice BARATO. Su solicitud no consume el limite de por
                  vida del SAT. Dice que existe, cuando y por cuanto.
    cfdi      ->  copia CARA. Trae el XML completo, pero las solicitudes de
                  este tipo tienen tope de por vida por periodo.

Con el indice guardado se localiza el mes exacto y se gasta UNA solicitud
`cfdi` precisa. Sin el, hay que adivinar el rango y cada intento quema un
recurso que no se renueva.

PROPIEDAD DE COLUMNAS (igual que en recibidas, a proposito)

    Hechos de emision   folio_fiscal, rfc_receptor, nombre_receptor,
                        fecha_emision, monto_total, efecto_comprobante,
                        rfc_pac
                        -> los escribe la ingesta la primera vez y NUNCA
                           los vuelve a pisar.

    Hechos de estatus   sat_estado, fecha_cancelacion
                        -> el barrido de metadata vuelve a pasar por rangos
                           ya ingeridos; si la fila se saltara entera, las
                           cancelaciones nuevas nunca entrarian.
"""

from __future__ import annotations

import logging

from sqlalchemy import func, literal_column
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.core.config import settings
from app.services.parser_metadata import ROL_EMISOR, parsear_metadata

logger = logging.getLogger(__name__)

# Un paquete del SAT trae hasta 10,000 comprobantes. Mandarlos en una sola
# sentencia hace un statement enorme y dificil de diagnosticar.
TAMANIO_LOTE = 500

COLUMNAS_ESTATUS = ("sat_estado", "fecha_cancelacion")

MAX_RECHAZOS_REPORTADOS = 20


def _en_lotes(secuencia, tamanio):
    for inicio in range(0, len(secuencia), tamanio):
        yield secuencia[inicio:inicio + tamanio]


def _resumen_rechazos(rechazadas: list[dict]) -> str:
    if not rechazadas:
        return ""

    from collections import Counter
    # Agrupado por motivo: 500 filas con el mismo problema son un problema,
    # no 500.
    conteo = Counter(r["motivo"].split(":")[0] for r in rechazadas)
    lineas = [f"{len(rechazadas)} fila(s) rechazada(s):"]
    lineas += [f"  {motivo} x{n}" for motivo, n in conteo.most_common()]

    lineas.append("Ejemplos:")
    for r in rechazadas[:MAX_RECHAZOS_REPORTADOS]:
        lineas.append(f"  [{r['archivo']}] {r['motivo']}")

    return "\n".join(lineas)


def ingerir_metadata_emitidas(
    db: Session,
    contenido_zip: bytes,
    id_solicitud: int | None = None,
) -> tuple[int, int, str]:
    """
    Parsea el ZIP de metadata de emitidas y llena MetadataEmitidas.

    Devuelve (nuevas, actualizadas, texto_rechazos).
    Idempotente: correr dos veces el mismo paquete deja la base igual.
    """
    from app.modelos.metadata_emitida import MetadataEmitidas

    validas, rechazadas = parsear_metadata(
        contenido_zip, rol=ROL_EMISOR, rfc_propio=settings.RFC_EMPRESA
    )

    if rechazadas:
        logger.warning(
            "Metadata de emitidas: %d fila(s) rechazada(s). Primera: %s",
            len(rechazadas), rechazadas[0]["motivo"],
        )

    if not validas:
        return 0, 0, _resumen_rechazos(rechazadas)

    # El UUID se normaliza igual que en el resto del proyecto: mayusculas y
    # sin espacios. Si aqui entrara en minusculas, la comparacion contra
    # Facturas.folio_fiscal volveria a fallar en silencio.
    from app.services.factura_service import normalizar_uuid

    filas = []
    for fila in validas:
        registro = dict(fila)
        registro["folio_fiscal"] = normalizar_uuid(registro["folio_fiscal"])
        registro["id_solicitud"] = id_solicitud
        filas.append(registro)

    nuevas = 0
    actualizadas = 0

    for lote in _en_lotes(filas, TAMANIO_LOTE):
        sentencia = insert(MetadataEmitidas).values(lote)

        campos = {
            columna: getattr(sentencia.excluded, columna)
            for columna in COLUMNAS_ESTATUS
        }
        # onupdate= del modelo no dispara en un INSERT ... ON CONFLICT.
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
        "Metadata de emitidas ingerida: %d nuevas, %d actualizadas, %d rechazadas",
        nuevas, actualizadas, len(rechazadas),
    )

    return nuevas, actualizadas, _resumen_rechazos(rechazadas)


# ---------------------------------------------------------------------------
# Reconciliacion: lo que el SAT dice contra lo que tenemos
# ---------------------------------------------------------------------------

def cobertura_por_mes(db: Session, incluir_canceladas: bool = False) -> list[dict]:
    """
    Mes por mes: cuantas emitidas dice el SAT y cuantas tenemos en `Facturas`.

    Es la red de seguridad que faltaba. Un mes con faltantes > 0 significa que
    hay comprobantes fiscales que el SAT registro y el sistema no vio, sea
    porque el correo se perdio o porque nunca llegó.

    Las canceladas se excluyen por defecto: no tenerlas no es una falta.
    """
    from app.modelos.factura import Facturas
    from app.modelos.metadata_emitida import MetadataEmitidas

    mes_sat = func.to_char(MetadataEmitidas.fecha_emision, "YYYY-MM")
    consulta_sat = db.query(
        mes_sat.label("mes"),
        func.count(MetadataEmitidas.id).label("total"),
        func.count(MetadataEmitidas.id)
        .filter(MetadataEmitidas.folio_fiscal.notin_(
            db.query(Facturas.folio_fiscal)
        ))
        .label("faltantes"),
    )
    if not incluir_canceladas:
        consulta_sat = consulta_sat.filter(MetadataEmitidas.sat_estado != "Cancelado")

    filas_sat = {
        fila.mes: (fila.total, fila.faltantes)
        for fila in consulta_sat.group_by(mes_sat).all()
    }

    mes_local = func.to_char(Facturas.fecha, "YYYY-MM")
    filas_locales = dict(
        db.query(mes_local, func.count(Facturas.id_factura))
        .group_by(mes_local)
        .all()
    )

    meses = sorted(set(filas_sat) | set(filas_locales))
    salida = []
    for mes in meses:
        total_sat, faltantes = filas_sat.get(mes, (0, 0))
        salida.append({
            "mes": mes,
            "sat_dice": total_sat,
            "tenemos": filas_locales.get(mes, 0),
            "faltantes": faltantes,
            # Sin metadata de ese mes no se puede afirmar nada: 0 faltantes
            # con 0 del SAT solo significa que no lo hemos consultado.
            "metadata_consultada": total_sat > 0,
        })
    return salida


def faltantes(
    db: Session,
    mes: str | None = None,
    limite: int = 200,
    incluir_canceladas: bool = False,
) -> list[dict]:
    """
    Los comprobantes que el SAT registro y no estan en `Facturas`.

    Cada uno trae su fecha de emision, que es el dato con el que se decide
    el rango exacto de la solicitud `cfdi`.
    """
    from app.modelos.factura import Facturas
    from app.modelos.metadata_emitida import MetadataEmitidas

    consulta = db.query(MetadataEmitidas).filter(
        MetadataEmitidas.folio_fiscal.notin_(db.query(Facturas.folio_fiscal))
    )
    if not incluir_canceladas:
        consulta = consulta.filter(MetadataEmitidas.sat_estado != "Cancelado")
    if mes:
        consulta = consulta.filter(
            func.to_char(MetadataEmitidas.fecha_emision, "YYYY-MM") == mes
        )

    filas = consulta.order_by(MetadataEmitidas.fecha_emision.asc()).limit(limite).all()

    return [
        {
            "folio_fiscal": f.folio_fiscal,
            "fecha_emision": f.fecha_emision,
            "monto_total": f.monto_total,
            "rfc_receptor": f.rfc_receptor,
            "nombre_receptor": f.nombre_receptor,
            "efecto_comprobante": f.efecto_comprobante,
            "sat_estado": f.sat_estado,
            "mes": f.fecha_emision.strftime("%Y-%m") if f.fecha_emision else None,
        }
        for f in filas
    ]


def buscar_uuid(db: Session, folio_fiscal: str) -> dict:
    """
    Todo lo que se sabe de un UUID, en los tres lugares donde puede estar.

    Es la consulta que resuelve un pago huerfano: dice si el SAT conoce esa
    factura, de que mes es, y si ya la tenemos.
    """
    from app.modelos.factura import Facturas
    from app.modelos.metadata_emitida import MetadataEmitidas
    from app.modelos.cp_documento_relacionado import CPDocumentosRelacionados
    from app.services.factura_service import normalizar_uuid

    uuid = normalizar_uuid(folio_fiscal)

    factura = db.query(Facturas).filter(Facturas.folio_fiscal == uuid).first()
    meta = db.query(MetadataEmitidas).filter(
        MetadataEmitidas.folio_fiscal == uuid
    ).first()
    pagos = db.query(func.count(CPDocumentosRelacionados.id)).filter(
        CPDocumentosRelacionados.uuid_documento == uuid
    ).scalar() or 0

    if factura:
        recomendacion = "La factura ya esta en la base; no hace falta pedir nada al SAT"
    elif meta:
        recomendacion = (
            f"El SAT la registra en {meta.fecha_emision.strftime('%Y-%m')}: "
            f"pedir una solicitud cfdi de ese mes la recupera"
        )
    else:
        recomendacion = (
            "Ni en la base ni en la metadata consultada. Falta pedir metadata "
            "del periodo donde pudo emitirse antes de gastar una solicitud cfdi"
        )

    return {
        "folio_fiscal": uuid,
        "en_facturas": factura is not None,
        "en_metadata_sat": meta is not None,
        "pagos_que_la_referencian": pagos,
        "fecha_emision_sat": meta.fecha_emision if meta else None,
        "monto_sat": meta.monto_total if meta else None,
        "receptor_sat": meta.nombre_receptor if meta else None,
        "estado_sat": meta.sat_estado if meta else None,
        "mes_sugerido": (
            meta.fecha_emision.strftime("%Y-%m") if meta and meta.fecha_emision else None
        ),
        "recomendacion": recomendacion,
    }
