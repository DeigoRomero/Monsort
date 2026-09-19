r"""
Orquestador de la Descarga Masiva de CFDI del SAT.

Avanza cada solicitud UN paso por corrida, no la lleva hasta el final.
El scheduler llama a avanzar_solicitudes() cada pocos minutos y en cada
pasada empuja lo que se pueda. Eso hace el job corto, interrumpible y
capaz de sobrevivir a un reinicio a media espera.

MAQUINA DE ESTADOS

    NUEVA ──solicitar──> SOLICITADA ──verificar──> EN_PROCESO
                             │                          │
                             │                     (insiste)
                             ↓                          ↓
                         RECHAZADA                    LISTA
                        (5002/5005)                     │
                                                   descargar
                                                        ↓
                                                  DESCARGANDO
                                                        │
                                                    ingerir
                                                        ↓
                                                    INGESTADA

    Terminales: INGESTADA, VACIA, FALLIDA, RECHAZADA

    VACIA no es FALLIDA. Un rango sin comprobantes termino bien; marcarlo
    como fallo haria que el job lo reintentara para siempre.

Ubicacion sugerida: app/services/descarga_masiva_service.py
"""

from __future__ import annotations

import csv
import io
import json
import logging
import zipfile
from datetime import date, datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.services.sat_descarga_client import (
    CODIGO_CUOTA_AGOTADA,
    CODIGO_DUPLICADA,
    CODIGO_TOPE_MAXIMO,
    TIPO_CFDI,
    TIPO_METADATA,
    COMPROBANTE_EMITIDOS,
    COMPROBANTE_RECIBIDOS,
    ClienteSAT,
    ClienteSATFalso,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Estados propios
# ---------------------------------------------------------------------------

NUEVA = "NUEVA"
SOLICITADA = "SOLICITADA"
EN_PROCESO = "EN_PROCESO"
LISTA = "LISTA"
DESCARGANDO = "DESCARGANDO"
INGESTADA = "INGESTADA"
VACIA = "VACIA"
FALLIDA = "FALLIDA"
RECHAZADA = "RECHAZADA"

ESTADOS_ACTIVOS = (NUEVA, SOLICITADA, EN_PROCESO, LISTA, DESCARGANDO)
ESTADOS_TERMINALES = (INGESTADA, VACIA, FALLIDA, RECHAZADA)

# Codigos de los que no se sale insistiendo: son decisiones del SAT.
CODIGOS_RECHAZO = (CODIGO_CUOTA_AGOTADA, CODIGO_DUPLICADA, CODIGO_TOPE_MAXIMO)

# El SAT puede tardar de minutos a horas. Se insiste con espera creciente.
MAX_INTENTOS_VERIFICACION = 40
ESPERA_BASE_MINUTOS = 5
ESPERA_MAXIMA_MINUTOS = 60

RFC_EMISOR = "MSF140227BF7"


# ---------------------------------------------------------------------------
# Utilidades
# ---------------------------------------------------------------------------

def _ahora() -> datetime:
    return datetime.now(timezone.utc)


def _cargar_lista(texto: str | None) -> list[str]:
    if not texto:
        return []
    try:
        valor = json.loads(texto)
        return valor if isinstance(valor, list) else []
    except json.JSONDecodeError:
        return []


def _guardar_lista(valores: list[str]) -> str:
    return json.dumps(valores)


def _espera_requerida(intentos: int) -> timedelta:
    """
    Backoff creciente con tope. Evita martillar el servicio cuando el SAT
    esta tardando horas, sin dejar de revisar con regularidad.
    """
    minutos = min(ESPERA_BASE_MINUTOS * (2 ** min(intentos, 4)), ESPERA_MAXIMA_MINUTOS)
    return timedelta(minutes=minutos)


def _toca_verificar(solicitud) -> bool:
    if solicitud.fecha_ultimo_intento is None:
        return True
    transcurrido = _ahora() - solicitud.fecha_ultimo_intento
    return transcurrido >= _espera_requerida(solicitud.intentos_verificacion)


# ---------------------------------------------------------------------------
# Creacion de solicitudes
# ---------------------------------------------------------------------------

def crear_solicitud(
    db: Session,
    fecha_inicial: date,
    fecha_final: date,
    tipo_solicitud: str = TIPO_METADATA,
    tipo_comprobante: str = COMPROBANTE_EMITIDOS,
) -> object:
    """
    Registra una solicitud en estado NUEVA. No habla con el SAT todavia:
    de eso se encarga la siguiente corrida del orquestador.
    """
    from app.modelos.solicitud_sat import SolicitudesSAT

    duplicada = (
        db.query(SolicitudesSAT)
        .filter(
            SolicitudesSAT.fecha_inicial == fecha_inicial,
            SolicitudesSAT.fecha_final == fecha_final,
            SolicitudesSAT.tipo_solicitud == tipo_solicitud,
            SolicitudesSAT.tipo_comprobante == tipo_comprobante,
            SolicitudesSAT.estado.in_(ESTADOS_ACTIVOS),
        )
        .first()
    )
    if duplicada:
        logger.warning(
            "Ya existe la solicitud #%s para ese rango (%s). No se crea otra.",
            duplicada.id, duplicada.estado,
        )
        return duplicada

    if fecha_inicial > fecha_final:
        raise ValueError("fecha_inicial no puede ser mayor que fecha_final")

    solicitud = SolicitudesSAT(
        fecha_inicial=fecha_inicial,
        fecha_final=fecha_final,
        tipo_solicitud=tipo_solicitud,
        tipo_comprobante=tipo_comprobante,
        estado=NUEVA,
        intentos_verificacion=0,
        fecha_creacion=_ahora(),
    )
    db.add(solicitud)
    db.commit()

    logger.info(
        "Solicitud creada: %s a %s (%s)",
        fecha_inicial, fecha_final, tipo_solicitud,
    )
    return solicitud

# Ventana movil del barrido diario. 90 dias cubre practicamente todas
# las cancelaciones: casi todas ocurren en los primeros meses. Subirlo
# da mas cobertura a cambio de paquetes mas grandes; bajarlo arriesga
# perder cancelaciones tardias, que el SOAP del paso 9 tendria que
# alcanzar en lotes mucho mas lentos.
DIAS_VENTANA_MOVIL = 90


def crear_solicitud_ventana_movil(
    db: Session,
    dias: int = DIAS_VENTANA_MOVIL,
) -> object | None:
    """
    Crea la solicitud diaria de recibidas sobre una ventana movil.

    La metadata trae el Estatus ACTUAL, no el del momento de emision:
    volver a descargar un rango ya ingerido refresca las cancelaciones
    de miles de facturas con una sola solicitud. La ingesta es
    idempotente, asi que el traslape no duplica nada.

    Devuelve None si no habia nada que hacer.
    """
    from app.modelos.solicitud_sat import SolicitudesSAT

    # 1. No apilar. Una solicitud puede tardar horas; si la de ayer sigue
    #    en curso, crear otra solo gasta cuota diaria del RFC.
    activa = (
        db.query(SolicitudesSAT)
        .filter(
            SolicitudesSAT.tipo_comprobante == COMPROBANTE_RECIBIDOS,
            SolicitudesSAT.estado.in_(ESTADOS_ACTIVOS),
        )
        .first()
    )
    if activa:
        logger.info(
            "Barrido diario omitido: la solicitud #%s sigue en %s",
            activa.id, activa.estado,
        )
        return None

    # 2. Una por dia. Protege contra un reinicio del proceso o un
    #    misfire de APScheduler que dispare el job dos veces.
    inicio_de_hoy = _ahora().replace(hour=0, minute=0, second=0, microsecond=0)
    ya_hubo = (
        db.query(SolicitudesSAT)
        .filter(
            SolicitudesSAT.tipo_comprobante == COMPROBANTE_RECIBIDOS,
            SolicitudesSAT.fecha_creacion >= inicio_de_hoy,
        )
        .first()
    )
    if ya_hubo:
        logger.info("Barrido diario ya ejecutado hoy (solicitud #%s)", ya_hubo.id)
        return None

    fecha_final = date.today()
    fecha_inicial = fecha_final - timedelta(days=dias)

    solicitud = crear_solicitud(
        db, fecha_inicial, fecha_final, TIPO_METADATA, COMPROBANTE_RECIBIDOS
    )
    logger.info(
        "Barrido diario de recibidas: %s a %s (solicitud #%s)",
        fecha_inicial, fecha_final, solicitud.id,
    )
    return solicitud


def crear_solicitudes_por_mes(
    db: Session,
    anio: int,
    tipo_solicitud: str = TIPO_METADATA,
    tipo_comprobante: str = COMPROBANTE_EMITIDOS
) -> list:
    """
    Parte un anio en solicitudes mensuales.

    Se trocea por mes y no por anio porque un rango grande produce paquetes
    enormes y porque, si algo falla, se pierde un mes y no doce.
    """
    creadas = []
    for mes in range(1, 13):
        inicio = date(anio, mes, 1)
        fin = (date(anio + 1, 1, 1) if mes == 12 else date(anio, mes + 1, 1))
        fin = fin - timedelta(days=1)
        creadas.append(crear_solicitud(db, inicio, fin, tipo_solicitud, tipo_comprobante))
    return creadas


# ---------------------------------------------------------------------------
# Transiciones
# ---------------------------------------------------------------------------

def _paso_solicitar(db: Session, solicitud, cliente: ClienteSAT) -> str:
    respuesta = cliente.solicitar_descarga(
        solicitud.fecha_inicial,
        solicitud.fecha_final,
        solicitud.tipo_solicitud,
        solicitud.tipo_comprobante,
    )

    solicitud.codigo_estatus = respuesta.codigo_estatus
    solicitud.mensaje_sat = respuesta.mensaje
    solicitud.fecha_ultimo_intento = _ahora()

    if respuesta.exitoso:
        solicitud.id_solicitud_sat = respuesta.id_solicitud
        solicitud.estado = SOLICITADA
        logger.info("Solicitud aceptada por el SAT: %s", respuesta.id_solicitud)
        return SOLICITADA

    if respuesta.codigo_estatus in CODIGOS_RECHAZO:
        solicitud.estado = RECHAZADA
        solicitud.fecha_completada = _ahora()
        logger.warning(
            "Solicitud rechazada (%s): %s",
            respuesta.codigo_estatus, respuesta.mensaje,
        )
        return RECHAZADA

    solicitud.estado = FALLIDA
    solicitud.fecha_completada = _ahora()
    logger.error("Fallo al solicitar: %s", respuesta.mensaje)
    return FALLIDA


def _paso_verificar(db: Session, solicitud, cliente: ClienteSAT) -> str:
    respuesta = cliente.verificar_solicitud(solicitud.id_solicitud_sat)

    solicitud.intentos_verificacion += 1
    solicitud.fecha_ultimo_intento = _ahora()
    solicitud.estado_solicitud_sat = respuesta.estado_solicitud
    solicitud.codigo_estatus = respuesta.codigo_estatus
    solicitud.mensaje_sat = respuesta.mensaje

    if not respuesta.exitoso:
        if solicitud.intentos_verificacion >= MAX_INTENTOS_VERIFICACION:
            solicitud.estado = FALLIDA
            solicitud.fecha_completada = _ahora()
            return FALLIDA
        return solicitud.estado

    if respuesta.fracaso_definitivo:
        solicitud.estado = FALLIDA
        solicitud.fecha_completada = _ahora()
        logger.error(
            "Solicitud %s en estado terminal del SAT: %s",
            solicitud.id_solicitud_sat, respuesta.estado_solicitud,
        )
        return FALLIDA

    if respuesta.esta_lista:
        solicitud.numero_cfdis = respuesta.numero_cfdis

        # Rango sin comprobantes: termino BIEN, no es un fallo.
        if not respuesta.paquetes:
            solicitud.estado = VACIA
            solicitud.fecha_completada = _ahora()
            logger.info(
                "Solicitud %s sin comprobantes en el rango",
                solicitud.id_solicitud_sat,
            )
            return VACIA

        solicitud.paquetes = _guardar_lista(respuesta.paquetes)
        solicitud.estado = LISTA
        logger.info(
            "Solicitud %s lista: %d CFDI en %d paquete(s)",
            solicitud.id_solicitud_sat,
            respuesta.numero_cfdis,
            len(respuesta.paquetes),
        )
        return LISTA

    if solicitud.intentos_verificacion >= MAX_INTENTOS_VERIFICACION:
        solicitud.estado = FALLIDA
        solicitud.fecha_completada = _ahora()
        logger.error(
            "Solicitud %s agoto los %d intentos de verificacion",
            solicitud.id_solicitud_sat, MAX_INTENTOS_VERIFICACION,
        )
        return FALLIDA

    solicitud.estado = EN_PROCESO
    return EN_PROCESO


def _paso_descargar(db: Session, solicitud, cliente: ClienteSAT) -> str:
    """
    Descarga UN paquete pendiente por corrida. Si quedan mas, la solicitud
    se queda en DESCARGANDO y la siguiente pasada toma el siguiente.
    """
    pendientes = [
        p for p in _cargar_lista(solicitud.paquetes)
        if p not in _cargar_lista(solicitud.paquetes_descargados)
    ]

    if not pendientes:
        solicitud.estado = INGESTADA
        solicitud.fecha_completada = _ahora()
        return INGESTADA

    id_paquete = pendientes[0]
    respuesta = cliente.descargar_paquete(id_paquete)
    solicitud.fecha_ultimo_intento = _ahora()

    if not respuesta.exitoso or not respuesta.contenido:
        solicitud.estado = FALLIDA
        solicitud.fecha_completada = _ahora()
        solicitud.error_ingesta = f"Fallo al descargar {id_paquete}: {respuesta.mensaje}"
        logger.error(solicitud.error_ingesta)
        return FALLIDA

    try:
        if solicitud.tipo_comprobante == COMPROBANTE_RECIBIDOS:
            from app.services.ingesta_recibidas_service import (
                ingerir_metadata_recibidas,
            )
            if solicitud.tipo_solicitud != TIPO_METADATA:
                raise ValueError(
                    "Las recibidas solo se soportan con tipo_solicitud=metadata"
                )
            nuevos, duplicados, rechazos = ingerir_metadata_recibidas(
                db, respuesta.contenido, solicitud.id
            )
            if rechazos:
                solicitud.error_ingesta = rechazos
        elif solicitud.tipo_solicitud == TIPO_METADATA:
            nuevos, duplicados = ingerir_metadata(db, respuesta.contenido)
        else:
            nuevos, duplicados = ingerir_cfdi(db, respuesta.contenido)
    except Exception as error:  # noqa: BLE001
        solicitud.estado = FALLIDA
        solicitud.fecha_completada = _ahora()
        solicitud.error_ingesta = f"Fallo al ingerir {id_paquete}: {error}"
        logger.exception("Fallo al ingerir %s", id_paquete)
        return FALLIDA

    solicitud.cfdis_nuevos = (solicitud.cfdis_nuevos or 0) + nuevos
    solicitud.cfdis_duplicados = (solicitud.cfdis_duplicados or 0) + duplicados

    descargados = _cargar_lista(solicitud.paquetes_descargados)
    descargados.append(id_paquete)
    solicitud.paquetes_descargados = _guardar_lista(descargados)

    logger.info(
        "Paquete %s ingerido: %d nuevos, %d duplicados",
        id_paquete, nuevos, duplicados,
    )

    if len(descargados) >= len(_cargar_lista(solicitud.paquetes)):
        solicitud.estado = INGESTADA
        solicitud.fecha_completada = _ahora()
        return INGESTADA

    solicitud.estado = DESCARGANDO
    return DESCARGANDO


# ---------------------------------------------------------------------------
# Ingesta
# ---------------------------------------------------------------------------

def ingerir_metadata(db: Session, contenido_zip: bytes) -> tuple[int, int]:
    """
    El paquete de metadata trae un unico .txt delimitado por '~', con
    encabezado. NO es XML.

    De momento solo se contabiliza y se registra: la metadata sirve para
    saber que comprobantes existen y su estatus, pero no trae los datos
    completos de la factura. La ingesta real va con tipo 'cfdi'.
    """
    nuevos = 0
    conocidos = 0

    from app.modelos.factura import Facturas

    with zipfile.ZipFile(io.BytesIO(contenido_zip)) as paquete:
        for nombre in paquete.namelist():
            texto = paquete.read(nombre).decode("utf-8", errors="replace")
            lector = csv.DictReader(io.StringIO(texto), delimiter="~")

            for fila in lector:
                uuid = (fila.get("Uuid") or "").strip()
                if not uuid:
                    continue

                existe = (
                    db.query(Facturas.id_factura)
                    .filter(Facturas.folio_fiscal == uuid)
                    .first()
                )
                if existe:
                    conocidos += 1
                else:
                    nuevos += 1

    return nuevos, conocidos


def ingerir_cfdi(db: Session, contenido_zip: bytes) -> tuple[int, int]:
    """
    El paquete de CFDI trae un .xml por comprobante.

    Reutiliza extraer_datos_xml() del flujo de Gmail sin cambios: es el
    mismo CFDI 4.0.

    Diferencias frente al flujo de correo:
      - NO hay PDF. El SAT solo guarda XML.
      - NO hay orden de compra. No es documento fiscal.
      - origen = 'sat'
    """
    from app.modelos.factura import Facturas
    from app.modelos.conceptos import Conceptos
    from app.services.factura_service import extraer_datos_xml, resolver_cliente
    from app.services.usuario_service import obtener_usuario_sistema, obtener_estado

    usuario = obtener_usuario_sistema(db)
    estado_captura = obtener_estado(db, "Requiere captura manual")
    estado_pendiente = obtener_estado(db, "Pendiente de factura")

    nuevos = 0
    duplicados = 0

    with zipfile.ZipFile(io.BytesIO(contenido_zip)) as paquete:
        for nombre in paquete.namelist():
            if not nombre.lower().endswith(".xml"):
                continue

            xml_bytes = paquete.read(nombre)

            try:
                datos = extraer_datos_xml(xml_bytes)
            except Exception as error:  # noqa: BLE001
                logger.warning("XML no parseable %s: %s", nombre, error)
                continue

            uuid = datos["folio_fiscal"]

            existe = (
                db.query(Facturas)
                .filter(Facturas.folio_fiscal == uuid)
                .first()
            )
            if existe:
                # Si ya la teniamos por Gmail pero sin XML, se completa.
                if existe.xml_factura is None:
                    existe.xml_factura = xml_bytes
                duplicados += 1
                continue

            estado = estado_pendiente if datos["numero_oc"] else estado_captura
            cliente_obj = resolver_cliente(db, datos["rfc"], datos["nombre_receptor"])

            factura = Facturas(
                folio_fiscal=uuid,
                folio_interno=datos["folio_interno"],
                rfc=datos["rfc"],
                cliente=datos["nombre_receptor"],
                fecha=datetime.fromisoformat(datos["fecha"]).date(),
                subtotal=float(datos["subtotal"]) if datos["subtotal"] else None,
                iva=float(datos["iva"]) if datos["iva"] else None,
                total=float(datos["total"]) if datos["total"] else None,
                moneda=datos["moneda"],
                tipo_cambio=datos["tipo_cambio"],
                numero_oc=datos["numero_oc"],
                numero_oc_detectado=datos["numero_oc"],
                xml_factura=xml_bytes,
                pdf_factura=None,            # el SAT no entrega PDF
                orden_compra_archivo=None,   # la OC no es documento fiscal
                message_id=None,
                origen="sat",
                id_usuario=usuario.id_usuario,
                id_estado=estado.id_estado,
                id_cliente=cliente_obj.id if cliente_obj else None,
            )
            db.add(factura)

            for concepto in datos["conceptos"]:
                db.add(Conceptos(
                    descripcion=concepto["descripcion"],
                    cantidad=float(concepto["cantidad"]) if concepto["cantidad"] else None,
                    unidad=concepto["unidad"],
                    precio_unitario=float(concepto["precio_unitario"]) if concepto["precio_unitario"] else None,
                    importe=float(concepto["importe"]) if concepto["importe"] else None,
                    factura=factura,
                ))

            db.flush()
            nuevos += 1

    return nuevos, duplicados


# ---------------------------------------------------------------------------
# Orquestador
# ---------------------------------------------------------------------------

def avanzar_solicitudes(
    db: Session,
    cliente: ClienteSAT | None = None,
    limite: int = 10,
) -> dict:
    """
    Avanza UN paso cada solicitud activa. Commit por solicitud.

    Sin cliente, usa el falso: permite ejercitar toda la maquina de
    estados sin e.firma.
    """
    from app.modelos.solicitud_sat import SolicitudesSAT

    if cliente is None:
        cliente = ClienteSATFalso()
        logger.warning("Usando ClienteSATFalso: no hay e.firma configurada")

    solicitudes = (
        db.query(SolicitudesSAT)
        .filter(SolicitudesSAT.estado.in_(ESTADOS_ACTIVOS))
        .order_by(SolicitudesSAT.fecha_creacion.asc())
        .limit(limite)
        .all()
    )

    resumen = {
        "revisadas": len(solicitudes),
        "avanzadas": 0,
        "en_espera": 0,
        "errores": 0,
        "transiciones": [],
    }

    for solicitud in solicitudes:
        estado_previo = solicitud.estado

        try:
            if solicitud.estado == NUEVA:
                nuevo = _paso_solicitar(db, solicitud, cliente)

            elif solicitud.estado in (SOLICITADA, EN_PROCESO):
                if not _toca_verificar(solicitud):
                    resumen["en_espera"] += 1
                    continue
                nuevo = _paso_verificar(db, solicitud, cliente)

            elif solicitud.estado in (LISTA, DESCARGANDO):
                nuevo = _paso_descargar(db, solicitud, cliente)

            else:
                continue

            db.commit()
            if nuevo != estado_previo:
                resumen["avanzadas"] += 1
                resumen["transiciones"].append(
                    f"#{solicitud.id}: {estado_previo} -> {nuevo}"
                )
            else:
                resumen["en_espera"] += 1

        except Exception as error:  # noqa: BLE001
            db.rollback()
            resumen["errores"] += 1
            logger.exception("Error avanzando solicitud #%s: %s", solicitud.id, error)

    return resumen


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    import argparse

    logging.basicConfig(level=logging.INFO, format="%(levelname)-8s %(message)s")

    parser = argparse.ArgumentParser(
        description="Descarga masiva de CFDI del SAT."
    )
    parser.add_argument("--crear-mes", help="Crear solicitud: AAAA-MM")
    parser.add_argument("--crear-anio", type=int, help="Crear 12 solicitudes")
    parser.add_argument("--tipo", default=TIPO_METADATA,
                        choices=[TIPO_METADATA, TIPO_CFDI])
    parser.add_argument("--comprobante", default=COMPROBANTE_EMITIDOS,
                        choices=[COMPROBANTE_EMITIDOS, COMPROBANTE_RECIBIDOS],
                        help="De quien son los comprobantes")
    parser.add_argument("--avanzar", action="store_true",
                        help="Avanzar un paso las solicitudes activas")
    parser.add_argument("--ciclo", type=int, metavar="N",
                        help="Avanzar N veces seguidas (solo con cliente falso)")
    parser.add_argument("--listar", action="store_true")
    args = parser.parse_args()

    from app.modelos import (
        usuario, factura, estados, configuracion, conceptos,
        complemento_pago, orden_compra, cp_documento_relacionado,
        correo_procesado, cliente, solicitud_sat, facturas_recibidas,
    )
    from app.BaseDeDatos import SessionLocal
    from app.modelos.solicitud_sat import SolicitudesSAT

    db = SessionLocal()
    try:
        if args.crear_mes:
            anio, mes = map(int, args.crear_mes.split("-"))
            inicio = date(anio, mes, 1)
            fin = (date(anio + 1, 1, 1) if mes == 12 else date(anio, mes + 1, 1))
            crear_solicitud(db, inicio, fin - timedelta(days=1),
                            args.tipo, args.comprobante)

        if args.crear_anio:
            crear_solicitudes_por_mes(db, args.crear_anio,
                                      args.tipo, args.comprobante)

        if args.avanzar or args.ciclo:
            from app.services.sat_descarga_client_real import construir_cliente_sat
            cliente = construir_cliente_sat()
            if cliente is None:
                print("Sin e.firma: usando ClienteSATFalso")
            else:
                print(f"Cliente SAT REAL conectado (RFC {cliente.rfc})")
            vueltas = args.ciclo or 1
            for vuelta in range(1, vueltas + 1):
                resumen = avanzar_solicitudes(db, cliente=cliente)
                print(f"\n--- vuelta {vuelta} ---")
                print(f"  revisadas={resumen['revisadas']} "
                      f"avanzadas={resumen['avanzadas']} "
                      f"en_espera={resumen['en_espera']} "
                      f"errores={resumen['errores']}")
                for transicion in resumen["transiciones"]:
                    print(f"  {transicion}")
                if resumen["revisadas"] == 0:
                    break

        if args.listar or args.avanzar or args.ciclo:
            print("\n" + "=" * 89)
            print(f"{'ID':<5}{'RANGO':<26}{'TIPO':<10}{'COMPROB':<11}"
                  f"{'ESTADO':<14}{'CFDIS':>7}{'NUEVOS':>8}")
            print("=" * 89)
            for s in db.query(SolicitudesSAT).order_by(SolicitudesSAT.id).all():
                rango = f"{s.fecha_inicial} a {s.fecha_final}"
                print(f"{s.id:<5}{rango:<26}{s.tipo_solicitud:<10}"
                      f"{s.tipo_comprobante:<11}{s.estado:<14}"
                      f"{s.numero_cfdis or 0:>7}{s.cfdis_nuevos or 0:>8}")
            print()
    finally:
        db.close()


if __name__ == "__main__":
    main()