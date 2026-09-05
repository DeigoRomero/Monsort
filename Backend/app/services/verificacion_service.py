r"""
Orquestador de verificacion de estatus ante el SAT.

Selecciona facturas candidatas, las consulta una por una y aplica los
resultados en tres buckets:

  1. CONCLUYENTE CON CAMBIO   -> actualiza id_estado, escribe historial,
                                 guarda columnas espejo, resetea contador
  2. CONCLUYENTE SIN CAMBIO   -> guarda columnas espejo y timestamp
  3. FALLO DE VERIFICACION    -> incrementa contador y timestamp.
                                 NO toca id_estado ni columnas espejo.

JERARQUIA DE AUTORIDAD (decision de negocio, explicita):
    El SAT esta por encima del estado local. Si el SAT dice "Cancelado"
    y la factura esta en cualquier otro estado -incluso terminal como
    Revisada o Historico-, la factura pasa a Cancelada. El SAT es la
    autoridad fiscal; lo que capturo el empleado no la contradice.

Uso (desde Backend/):

    # dry-run: consulta el SAT pero hace rollback al final
    python -m app.services.verificacion_service --limite 20

    # aplica de verdad
    python -m app.services.verificacion_service --limite 20 --apply

    # una factura concreta
    python -m app.services.verificacion_service --uuid CDDA6C1B-... --apply

Ubicacion: app/services/verificacion_service.py
"""

from __future__ import annotations

import argparse
import logging
import time
from datetime import datetime, timezone, date

from sqlalchemy.orm import Session, load_only
from app.services.usuario_service import obtener_usuario_sistema
from app.services.sat_consulta_service import ResultadoSat, consultar
from app.services.factura_service import _ids_estados_terminales
from app.services.factura_service import _obtener_id_estado_cancelada

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# >>> AJUSTA SI TUS NOMBRES DIFIEREN <<<
# ---------------------------------------------------------------------------

RFC_EMISOR = "MSF140227BF7"        # Monsort, siempre el emisor
NOMBRE_ESTADO_CANCELADA = "Cancelada"

PAUSA_ENTRE_LLAMADAS = 1.5         # segundos. NUNCA concurrente.
LOTE_POR_DEFECTO = 50

# Circuit breaker: si tras revisar este minimo de facturas la tasa de
# fallo supera el umbral, se aborta. Un pico de 601 no es "facturas
# raras": es el codigo roto o el SAT cambiado.
MINIMO_PARA_EVALUAR_CORTE = 10
UMBRAL_FALLO_CORTE = 0.5

BUCKET_CAMBIO = "concluyente_con_cambio"
BUCKET_SIN_CAMBIO = "concluyente_sin_cambio"
BUCKET_FALLO = "fallo_verificacion"


# ---------------------------------------------------------------------------
# Helpers de esquema
# ---------------------------------------------------------------------------

def _importar_modelos():
    """
    SQLAlchemy resuelve los relationship() por string hasta que todas las
    clases estan registradas. Importar los modulos basta.
    """
    from app.modelos import (  # noqa: F401
        usuario, factura, estados, configuracion, conceptos,
        complemento_pago, orden_compra, cp_documento_relacionado,
        correo_procesado, cliente,
    )


def obtener_id_estado(db: Session, nombre: str) -> int:
    """Busca el id de un estado por nombre. Falla ruidosamente si no existe."""
    from app.modelos.estados import Estados

    estado = db.query(Estados).filter(Estados.nombre_estado == nombre).one_or_none()
    if estado is None:
        raise RuntimeError(
            f"No existe el estado '{nombre}' en la tabla Estados. "
            f"Ajusta NOMBRE_ESTADO_CANCELADA o el campo de nombre del modelo."
        )
    return estado.id_estado


def obtener_estados_terminales(db: Session) -> set[int]:
    """
    Devuelve los ids de estados terminales.

    Se intenta importar la constante que ya existe en el proyecto; si la
    ruta cambio, se cae a una lista por nombre.
    """
    from app.modelos.estados import Estados

    try:
        from app.core.constantes import ESTADOS_TERMINALES  # type: ignore
        nombres = set(ESTADOS_TERMINALES)
    except ImportError:
        nombres = {"Cancelada", "Revisada", "Histórico"}

    filas = db.query(Estados).filter(Estados.nombre_estado.in_(nombres)).all()
    return {e.id_estado for e in filas}


# ---------------------------------------------------------------------------
# Historial
# ---------------------------------------------------------------------------

def registrar_historial(
    db: Session, factura, resultado: ResultadoSat, id_estado_nuevo: int
) -> None:
    """Escribe la auditoria de la cancelacion detectada por el SAT."""
    from app.modelos.factura import HistorialVerificacion

    detalle = (
        f"SAT: {resultado.estado}"
        f" | EsCancelable: {resultado.es_cancelable}"
        f" | EstatusCancelacion: {resultado.estatus_cancelacion}"
    )

    db.add(
        HistorialVerificacion(
            id_factura=factura.id_factura,
            id_estado=id_estado_nuevo,
            id_usuario=obtener_usuario_sistema(db).id_usuario,
            fecha_verificacion=date.today(),
            resultado_verificacion=detalle,
            origen="sat",
        )
    )


# ---------------------------------------------------------------------------
# Seleccion de candidatos
# ---------------------------------------------------------------------------

def seleccionar_candidatos(db: Session, limite: int, incluir_terminales: bool = False) -> list:
    """
    Round-robin por antiguedad de verificacion.

    Excluye:
      - estados terminales (no van a cambiar por el ciclo recurrente)
      - facturas sin total o con total <= 0 (inconsultables: 13 de las
        718 historicas caen aqui y solo desperdiciarian llamadas)

    Ordena por fecha_ultima_verificacion_sat ascendente con nulls
    primero, de modo que las nunca verificadas van al frente y el ciclo
    cubre todo el universo sin logica de prioridades adicional.
    """
    from app.modelos.factura import Facturas

    terminales = set(_ids_estados_terminales(db))

    consulta = (
        db.query(Facturas)
        .options(
            # Nunca cargar los blobs en un listado.
            load_only(
                Facturas.id_factura,
                Facturas.folio_fiscal,
                Facturas.rfc,
                Facturas.total,
                Facturas.id_estado,
                Facturas.origen,
                Facturas.sat_estado,
                Facturas.fecha_ultima_verificacion_sat,
                Facturas.intentos_verificacion_fallidos,
            )
        )
        .filter(Facturas.folio_fiscal.isnot(None))
        .filter(Facturas.total.isnot(None))
        .filter(Facturas.total > 0)
    )

    if terminales and not incluir_terminales:
        consulta = consulta.filter(~Facturas.id_estado.in_(terminales))

    return (
        consulta.order_by(Facturas.fecha_ultima_verificacion_sat.asc().nullsfirst())
        .limit(limite)
        .all()
    )


def seleccionar_por_uuid(db: Session, uuid: str) -> list:
    """Sin filtros de estado: el endpoint manual debe poder forzar cualquiera."""
    from app.modelos.factura import Facturas

    return (
        db.query(Facturas)
        .filter(Facturas.folio_fiscal.ilike(uuid))
        .all()
    )


# ---------------------------------------------------------------------------
# Verificacion de una factura
# ---------------------------------------------------------------------------

def verificar_factura(db: Session, factura, id_estado_cancelada: int) -> str:
    """
    Consulta una factura y aplica el resultado. Devuelve el bucket.

    NO hace commit: eso lo decide quien llama.
    """
    ahora = datetime.now(timezone.utc)

    resultado = consultar(
        rfc_emisor=RFC_EMISOR,
        rfc_receptor=factura.rfc,
        total=factura.total,
        uuid=factura.folio_fiscal,
        sello_ultimos_8=None,  # 'fe' es opcional; confirmado empiricamente
    )

    factura.fecha_ultima_verificacion_sat = ahora

    # ---------------- bucket 3: fallo ----------------
    if not resultado.es_concluyente:
        factura.intentos_verificacion_fallidos = (
            factura.intentos_verificacion_fallidos or 0
        ) + 1
        # Deliberadamente NO se escriben las columnas espejo ni id_estado.
        # Una respuesta 601 trae EsCancelable y EstatusCancelacion
        # poblados con basura.
        logger.warning(
            "Verificacion fallida %s: %s",
            factura.folio_fiscal,
            resultado.motivo_fallo,
        )
        return BUCKET_FALLO

    # ---------------- concluyente: espejo ----------------
    factura.sat_estado = resultado.estado
    factura.sat_es_cancelable = resultado.es_cancelable
    factura.sat_estatus_cancelacion = resultado.estatus_cancelacion
    factura.sat_codigo_estatus = resultado.codigo_estatus
    factura.sat_validacion_efos = resultado.validacion_efos
    factura.intentos_verificacion_fallidos = 0

    # ---------------- bucket 1: cambio de estado ----------------
    # El SAT esta por encima del estado local. Si dice Cancelado, la
    # factura pasa a Cancelada aunque estuviera en un estado terminal.
    if resultado.esta_cancelada and factura.id_estado != id_estado_cancelada:
        logger.info(
            "SAT reporta CANCELADA %s (estado local %s -> %s)",
            factura.folio_fiscal,
            factura.id_estado,
            id_estado_cancelada,
        )
        factura.id_estado = id_estado_cancelada
        registrar_historial(db, factura, resultado, id_estado_cancelada)
        return BUCKET_CAMBIO

    # ---------------- bucket 2: sin cambio ----------------
    return BUCKET_SIN_CAMBIO


# ---------------------------------------------------------------------------
# Lote
# ---------------------------------------------------------------------------

def verificar_lote(
    db: Session,
    limite: int = LOTE_POR_DEFECTO,
    uuid: str | None = None,
    aplicar: bool = False,
    incluir_terminales: bool = False,
) -> dict:
    """
    Verifica un lote de facturas.

    Con aplicar=False (default) se consulta el SAT de verdad pero se hace
    rollback al final: mismo patron dry-run que los scripts de migracion.

    Commit por factura, no por lote: si la numero 40 revienta, no se
    pierden las 39 anteriores.
    """
    _importar_modelos()

    id_estado_cancelada = _obtener_id_estado_cancelada(db)

    facturas = (
        seleccionar_por_uuid(db, uuid)
        if uuid
        else seleccionar_candidatos(db, limite, incluir_terminales)
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
            bucket = verificar_factura(db, factura, id_estado_cancelada)
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
                "Error verificando %s: %s", factura.folio_fiscal, error
            )

        # ---------------- circuit breaker ----------------
        if indice >= MINIMO_PARA_EVALUAR_CORTE:
            tasa = (resumen[BUCKET_FALLO] + resumen["errores"]) / indice
            if tasa > UMBRAL_FALLO_CORTE:
                logger.error(
                    "Circuit breaker: %.0f%% de fallos tras %d consultas. Abortando.",
                    tasa * 100,
                    indice,
                )
                resumen["abortado_por_circuit_breaker"] = True
                break

        if indice < len(facturas):
            time.sleep(PAUSA_ENTRE_LLAMADAS)

    if not aplicar:
        db.rollback()

    return resumen

def verificar_una(db: Session, id_factura: int, aplicar: bool = True) -> dict | None:
    """
    Verifica una sola factura por su PK. Devuelve None si no existe.

    Usada por el endpoint manual. Captura los valores ANTES de decidir
    commit o rollback, porque en dry-run el rollback revierte el objeto.
    """
    _importar_modelos()
    from app.modelos.factura import Facturas

    factura = (
        db.query(Facturas).filter(Facturas.id_factura == id_factura).one_or_none()
    )
    if factura is None:
        return None

    id_estado_cancelada = _obtener_id_estado_cancelada(db)
    estado_anterior = factura.id_estado

    try:
        bucket = verificar_factura(db, factura, id_estado_cancelada)

        instantanea = {
            "id_factura": factura.id_factura,
            "folio_fiscal": factura.folio_fiscal,
            "resultado": bucket,
            "cambio_aplicado": bucket == BUCKET_CAMBIO and aplicar,
            "id_estado_anterior": estado_anterior,
            "id_estado_actual": factura.id_estado,
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
    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)-8s %(message)s",
    )

    parser = argparse.ArgumentParser(
        description="Verifica el estatus de facturas ante el SAT."
    )
    parser.add_argument("--limite", type=int, default=LOTE_POR_DEFECTO)
    parser.add_argument("--uuid", help="Verificar una sola factura")
    parser.add_argument(
        "--apply",
        action="store_true",
        dest="aplicar",
        help="Commitear los cambios. Sin este flag hace rollback (dry-run).",
    )
    parser.add_argument("--incluir-terminales", action="store_true",
                    dest="incluir_terminales",
                    help="Ignorar el filtro de estados terminales (barrido historico)")
    args = parser.parse_args()

    _importar_modelos()
    from app.BaseDeDatos import SessionLocal

    db = SessionLocal()
    try:
        resumen = verificar_lote(db, limite=args.limite, uuid=args.uuid,
                         aplicar=args.aplicar,
                         incluir_terminales=args.incluir_terminales)
    finally:
        db.close()

    print()
    print("=" * 66)
    print("VERIFICACION SAT  " + ("[APLICADO]" if args.aplicar else "[DRY-RUN]"))
    print("=" * 66)
    print(f"  Seleccionadas:         {resumen['seleccionadas']}")
    print(f"  Cambio de estado:      {resumen[BUCKET_CAMBIO]}")
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