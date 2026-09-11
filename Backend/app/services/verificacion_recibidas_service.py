r"""
Verificacion de estatus ante el SAT para CFDI RECIBIDOS.

Modulo PARALELO a verificacion_service.py, no un refactor de este. La
parte riesgosa -el cliente SOAP y la regla del guardian- ya vive en
sat_consulta_service y se comparte tal cual; lo que difiere entre
emitidas y recibidas es casi todo lo demas:

    emitidas                          recibidas
    ------------------------------    ------------------------------
    re = Monsort (constante)          re = el proveedor
    rr = el cliente                   rr = Monsort (constante)
    Facturas.total                    FacturasRecibidas.monto_total
    cambia id_estado a Cancelada      no hay estados de negocio
    escribe HistorialVerificacion     sin historial (FK incompatible)

Meter esas diferencias en verificacion_service exigiria bifurcar por
tabla un archivo que corre desatendido a las 3:30 AM. El costo de un
modulo aparte son ~20 lineas repetidas; el beneficio es no tocar codigo
que ya funciona.

COMPLEMENTA AL BARRIDO DE METADATA, NO LO SUSTITUYE

    El barrido diario redescarga los ultimos 90 dias y refresca miles de
    estatus con UNA solicitud. Esto es una llamada SOAP POR UUID, asi que
    solo se usa para lo que queda FUERA de esa ventana: cancelaciones
    tardias, que son pocas.

    Por eso el default excluye las facturas recientes: verificarlas aqui
    gastaria llamadas en algo que la metadata ya resolvio esta madrugada.

Uso (desde backend/):

    python -m app.services.verificacion_recibidas_service --limite 20
    python -m app.services.verificacion_recibidas_service --limite 100 --apply
    python -m app.services.verificacion_recibidas_service --uuid CDDA6C1B-... --apply

Ubicacion: app/services/verificacion_recibidas_service.py
"""

from __future__ import annotations

import argparse
import logging
import time
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.services.sat_consulta_service import consultar

logger = logging.getLogger(__name__)

# Monsort es SIEMPRE el receptor aqui. Invertido respecto a emitidas.
RFC_RECEPTOR = "MSF140227BF7"

PAUSA_ENTRE_LLAMADAS = 1.5         # segundos. NUNCA concurrente.
LOTE_POR_DEFECTO = 100

# Dias que ya cubre el barrido diario de metadata. Las facturas emitidas
# dentro de esta ventana se omiten: su estatus se acaba de refrescar.
DIAS_CUBIERTOS_POR_METADATA = 90

# Mismo circuit breaker que emitidas: un pico de fallos no son "facturas
# raras", es el codigo roto o el servicio del SAT cambiado.
MINIMO_PARA_EVALUAR_CORTE = 10
UMBRAL_FALLO_CORTE = 0.5

BUCKET_CAMBIO = "concluyente_con_cambio"
BUCKET_SIN_CAMBIO = "concluyente_sin_cambio"
BUCKET_FALLO = "fallo_verificacion"

ESTADO_CANCELADO = "Cancelado"


def _importar_modelos() -> None:
    from app.modelos import (  # noqa: F401
        usuario, factura, estados, configuracion, conceptos,
        complemento_pago, orden_compra, cp_documento_relacionado,
        correo_procesado, cliente, solicitud_sat, facturas_recibidas,
    )


# ---------------------------------------------------------------------------
# Seleccion de candidatos
# ---------------------------------------------------------------------------

def seleccionar_candidatos(
    db: Session,
    limite: int,
    dias_cubiertos: int = DIAS_CUBIERTOS_POR_METADATA,
    incluir_canceladas: bool = False,
) -> list:
    """
    Round-robin por antiguedad de verificacion.

    Excluye:
      - las ya canceladas: es estado terminal ante el SAT, volver a
        preguntarlo gasta cuota para confirmar lo que ya sabemos
      - las de los ultimos `dias_cubiertos` dias: el barrido de metadata
        ya las refresco esta madrugada
      - monto <= 0: la expresion de consulta necesita 'tt' valido

    Ordena por fecha_ultima_verificacion_sat ascendente con nulls
    primero, de modo que las nunca verificadas van al frente y el ciclo
    cubre todo el universo sin logica de prioridades adicional.
    """
    from app.modelos.facturas_recibidas import FacturasRecibidas

    consulta = (
        db.query(FacturasRecibidas)
        .filter(FacturasRecibidas.folio_fiscal.isnot(None))
        .filter(FacturasRecibidas.monto_total.isnot(None))
        .filter(FacturasRecibidas.monto_total > 0)
    )

    if not incluir_canceladas:
        consulta = consulta.filter(
            (FacturasRecibidas.sat_estado.is_(None))
            | (FacturasRecibidas.sat_estado != ESTADO_CANCELADO)
        )

    if dias_cubiertos > 0:
        corte = datetime.now() - timedelta(days=dias_cubiertos)
        consulta = consulta.filter(FacturasRecibidas.fecha_emision < corte)

    return (
        consulta.order_by(
            FacturasRecibidas.fecha_ultima_verificacion_sat.asc().nullsfirst()
        )
        .limit(limite)
        .all()
    )


def seleccionar_por_uuid(db: Session, uuid: str) -> list:
    """Sin filtros: el endpoint manual debe poder forzar cualquiera."""
    from app.modelos.facturas_recibidas import FacturasRecibidas

    return (
        db.query(FacturasRecibidas)
        .filter(FacturasRecibidas.folio_fiscal.ilike(uuid))
        .all()
    )


# ---------------------------------------------------------------------------
# Verificacion de una factura
# ---------------------------------------------------------------------------

def verificar_factura_recibida(db: Session, factura) -> str:
    """
    Consulta una factura recibida y escribe sus columnas espejo.
    Devuelve el bucket. NO hace commit.

    A diferencia de emitidas, aqui no hay efecto de negocio: no se cambia
    ningun estado ni se escribe historial. El estatus ES el dato que el
    cliente pidio ver, y vive en las columnas espejo.
    """
    ahora = datetime.now(timezone.utc)

    # Expresion INVERTIDA respecto a emitidas: el emisor es el proveedor
    # y el receptor es Monsort.
    resultado = consultar(
        rfc_emisor=factura.rfc_emisor,
        rfc_receptor=RFC_RECEPTOR,
        total=factura.monto_total,
        uuid=factura.folio_fiscal,
        sello_ultimos_8=None,  # 'fe' es opcional; la metadata no lo trae
    )

    factura.fecha_ultima_verificacion_sat = ahora

    # ---------------- bucket 3: fallo ----------------
    if not resultado.es_concluyente:
        factura.intentos_verificacion_fallidos = (
            factura.intentos_verificacion_fallidos or 0
        ) + 1
        # Deliberadamente NO se escriben las columnas espejo: una
        # respuesta 601 trae EsCancelable y EstatusCancelacion poblados
        # con basura.
        logger.warning(
            "Verificacion fallida %s: %s",
            factura.folio_fiscal, resultado.motivo_fallo,
        )
        return BUCKET_FALLO

    # ---------------- concluyente: espejo ----------------
    estado_previo = factura.sat_estado

    factura.sat_estado = resultado.estado
    factura.sat_es_cancelable = resultado.es_cancelable
    factura.sat_estatus_cancelacion = resultado.estatus_cancelacion
    factura.sat_codigo_estatus = resultado.codigo_estatus
    factura.sat_validacion_efos = resultado.validacion_efos
    factura.intentos_verificacion_fallidos = 0

    # fecha_cancelacion NO se toca: el webservice de consulta no devuelve
    # la fecha, solo el estatus. Ese dato es propiedad de la metadata.

    if resultado.esta_cancelada and estado_previo != ESTADO_CANCELADO:
        logger.info(
            "SAT reporta CANCELADA la recibida %s de %s (antes: %s)",
            factura.folio_fiscal, factura.rfc_emisor, estado_previo,
        )
        return BUCKET_CAMBIO

    return BUCKET_SIN_CAMBIO


# ---------------------------------------------------------------------------
# Lote
# ---------------------------------------------------------------------------

def verificar_lote_recibidas(
    db: Session,
    limite: int = LOTE_POR_DEFECTO,
    uuid: str | None = None,
    aplicar: bool = False,
    dias_cubiertos: int = DIAS_CUBIERTOS_POR_METADATA,
    incluir_canceladas: bool = False,
) -> dict:
    """
    Verifica un lote de facturas recibidas.

    Con aplicar=False (default) consulta el SAT de verdad pero hace
    rollback al final: mismo patron dry-run que el resto de los scripts.

    Commit por factura, no por lote: si la numero 40 revienta, no se
    pierden las 39 anteriores.
    """
    _importar_modelos()

    facturas = (
        seleccionar_por_uuid(db, uuid)
        if uuid
        else seleccionar_candidatos(db, limite, dias_cubiertos, incluir_canceladas)
    )

    resumen = {
        "seleccionadas": len(facturas),
        BUCKET_CAMBIO: 0,
        BUCKET_SIN_CAMBIO: 0,
        BUCKET_FALLO: 0,
        "errores": 0,
        "canceladas_detectadas": [],
        "abortado_por_circuit_breaker": False,
        "aplicado": aplicar,
    }

    for indice, factura in enumerate(facturas, start=1):
        try:
            bucket = verificar_factura_recibida(db, factura)
            resumen[bucket] += 1
            if bucket == BUCKET_CAMBIO:
                resumen["canceladas_detectadas"].append(factura.folio_fiscal)

            if aplicar:
                db.commit()
            else:
                db.flush()

        except Exception as error:  # noqa: BLE001
            db.rollback()
            resumen["errores"] += 1
            logger.exception(
                "Error verificando recibida %s: %s", factura.folio_fiscal, error
            )

        # ---------------- circuit breaker ----------------
        if indice >= MINIMO_PARA_EVALUAR_CORTE:
            tasa = (resumen[BUCKET_FALLO] + resumen["errores"]) / indice
            if tasa > UMBRAL_FALLO_CORTE:
                logger.error(
                    "Circuit breaker (recibidas): %.0f%% de fallos tras %d "
                    "consultas. Abortando.",
                    tasa * 100, indice,
                )
                resumen["abortado_por_circuit_breaker"] = True
                break

        if indice < len(facturas):
            time.sleep(PAUSA_ENTRE_LLAMADAS)

    if not aplicar:
        db.rollback()

    return resumen


def verificar_una_recibida(
    db: Session, id_factura_recibida: int, aplicar: bool = True
) -> dict | None:
    """
    Verifica una sola factura recibida por su PK. None si no existe.

    Captura los valores ANTES de decidir commit o rollback: en dry-run el
    rollback revierte el objeto y la instantanea saldria vacia.
    """
    _importar_modelos()
    from app.modelos.facturas_recibidas import FacturasRecibidas

    factura = (
        db.query(FacturasRecibidas)
        .filter(FacturasRecibidas.id_factura_recibida == id_factura_recibida)
        .one_or_none()
    )
    if factura is None:
        return None

    estado_anterior = factura.sat_estado

    try:
        bucket = verificar_factura_recibida(db, factura)

        instantanea = {
            "id_factura_recibida": factura.id_factura_recibida,
            "folio_fiscal": factura.folio_fiscal,
            "resultado": bucket,
            "cambio_aplicado": bucket == BUCKET_CAMBIO and aplicar,
            "sat_estado_anterior": estado_anterior,
            "sat_estado": factura.sat_estado,
            "sat_es_cancelable": factura.sat_es_cancelable,
            "sat_estatus_cancelacion": factura.sat_estatus_cancelacion,
            "sat_codigo_estatus": factura.sat_codigo_estatus,
            "sat_validacion_efos": factura.sat_validacion_efos,
            "fecha_verificacion": factura.fecha_ultima_verificacion_sat,
        }

        if aplicar:
            db.commit()
        else:
            db.rollback()

        return instantanea

    except Exception:
        db.rollback()
        raise


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)-8s %(message)s")

    parser = argparse.ArgumentParser(
        description="Verifica el estatus de facturas RECIBIDAS ante el SAT."
    )
    parser.add_argument("--limite", type=int, default=LOTE_POR_DEFECTO)
    parser.add_argument("--uuid", help="Verificar una sola factura")
    parser.add_argument(
        "--apply", action="store_true", dest="aplicar",
        help="Commitear los cambios. Sin este flag hace rollback (dry-run).",
    )
    parser.add_argument(
        "--dias-cubiertos", type=int, default=DIAS_CUBIERTOS_POR_METADATA,
        dest="dias_cubiertos",
        help="Omitir facturas emitidas en los ultimos N dias (0 = ninguna).",
    )
    parser.add_argument(
        "--incluir-canceladas", action="store_true", dest="incluir_canceladas",
        help="No omitir las que ya estan canceladas.",
    )
    args = parser.parse_args()

    _importar_modelos()
    from app.BaseDeDatos import SessionLocal

    db = SessionLocal()
    try:
        resumen = verificar_lote_recibidas(
            db,
            limite=args.limite,
            uuid=args.uuid,
            aplicar=args.aplicar,
            dias_cubiertos=args.dias_cubiertos,
            incluir_canceladas=args.incluir_canceladas,
        )
    finally:
        db.close()

    print()
    print("=" * 66)
    print("VERIFICACION SAT - RECIBIDAS  "
          + ("[APLICADO]" if args.aplicar else "[DRY-RUN]"))
    print("=" * 66)
    print(f"  Seleccionadas:         {resumen['seleccionadas']}")
    print(f"  Cambio a cancelada:    {resumen[BUCKET_CAMBIO]}")
    print(f"  Sin cambio:            {resumen[BUCKET_SIN_CAMBIO]}")
    print(f"  Fallo de verificacion: {resumen[BUCKET_FALLO]}")
    print(f"  Errores:               {resumen['errores']}")

    if resumen["canceladas_detectadas"]:
        print()
        print("  Canceladas detectadas por el SAT:")
        for folio in resumen["canceladas_detectadas"]:
            print(f"    {folio}")

    if resumen["abortado_por_circuit_breaker"]:
        print()
        print("  *** ABORTADO POR CIRCUIT BREAKER ***")
        print("  Tasa de fallo excesiva. Revisa el codigo o el servicio.")

    if not args.aplicar:
        print()
        print("  DRY-RUN: no se guardo nada. Repite con --apply.")
    print()


if __name__ == "__main__":
    main()