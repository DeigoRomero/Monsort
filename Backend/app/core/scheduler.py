import logging

from apscheduler.schedulers.background import BackgroundScheduler

from app.BaseDeDatos import SessionLocal
from app.services.factura_service import procesar_correos_nuevos
from app.services.verificacion_service import verificar_lote
from app.services.descarga_masiva_service import avanzar_solicitudes, crear_solicitud_ventana_movil
from app.services.verificacion_recibidas_service import verificar_lote_recibidas


logger = logging.getLogger(__name__)


def job_procesar_correos():
    db = SessionLocal()
    try:
        procesar_correos_nuevos(db)
    except Exception:
        logger.exception("Error en el job de procesar correos")
    finally:
        db.close()


def job_verificar_sat():
    """Verifica un lote de facturas ante el SAT y sincroniza cancelaciones."""
    db = SessionLocal()
    try:
        resumen = verificar_lote(db, limite=100, aplicar=True)
        logger.info(
            "Verificación SAT: %d consultadas, %d canceladas detectadas, %d fallos",
            resumen["seleccionadas"],
            resumen["concluyente_con_cambio"],
            resumen["fallo_verificacion"],
        )
        if resumen["canceladas_detectadas"]:
            logger.warning(
                "Facturas canceladas por el SAT: %s",
                ", ".join(resumen["canceladas_detectadas"]),
            )
        if resumen["abortado_por_circuit_breaker"]:
            logger.error("Verificación SAT abortada por circuit breaker")
    except Exception:
        logger.exception("Error en el job de verificación SAT")
    finally:
        db.close()

def job_descarga_masiva():
    """Avanza un paso las solicitudes activas de descarga masiva."""
    db = SessionLocal()
    try:
        from app.services.sat_descarga_client_real import construir_cliente_sat
        cliente = construir_cliente_sat()
        resumen = avanzar_solicitudes(db, cliente=cliente)
        if resumen["transiciones"]:
            logger.info("Descarga masiva: %s", "; ".join(resumen["transiciones"]))
        if resumen["errores"]:
            logger.error("Descarga masiva: %d errores", resumen["errores"])
        if not resumen["transiciones"] and not resumen["errores"]:
            logger.info(
                "Descarga masiva: sin cambios (%d activa(s) revisada(s))",
                resumen.get("revisadas", 0),
            )
    except Exception:
        logger.exception("Error en el job de descarga masiva")
    finally:
        db.close()

def job_barrido_recibidas():
    """Crea la solicitud diaria de metadata de facturas recibidas."""
    db = SessionLocal()
    try:
        solicitud = crear_solicitud_ventana_movil(db)
        if solicitud:
            logger.info("Barrido de recibidas registrado: #%s", solicitud.id)
    except Exception:
        logger.exception("Error en el job de barrido de recibidas")
    finally:
        db.close()

def job_verificar_recibidas():
    """Verifica ante el SAT las recibidas fuera de la ventana de metadata."""
    db = SessionLocal()
    try:
        resumen = verificar_lote_recibidas(db, limite=100, aplicar=True)
        logger.info(
            "Verificación SAT recibidas: %d consultadas, %d canceladas, %d fallos",
            resumen["seleccionadas"],
            resumen["concluyente_con_cambio"],
            resumen["fallo_verificacion"],
        )
        if resumen["canceladas_detectadas"]:
            logger.warning(
                "Recibidas canceladas por el SAT: %s",
                ", ".join(resumen["canceladas_detectadas"]),
            )
        if resumen["abortado_por_circuit_breaker"]:
            logger.error("Verificación de recibidas abortada por circuit breaker")
    except Exception:
        logger.exception("Error en el job de verificación de recibidas")
    finally:
        db.close()


scheduler = BackgroundScheduler()

scheduler.add_job(
    job_procesar_correos,
    "interval",
    minutes=5,
    id="procesar_correos",
    max_instances=1,
    coalesce=True,
)

scheduler.add_job(
    job_verificar_sat,
    "cron",
    hour=3,
    minute=30,
    id="verificar_sat",
    max_instances=1,
    coalesce=True,
    misfire_grace_time=3600,
)

scheduler.add_job(
    job_descarga_masiva,
    "interval",
    minutes=10,
    id="descarga_masiva",
    max_instances=1,
    coalesce=True,
)

scheduler.add_job(
    job_barrido_recibidas,
    "cron",
    hour=2,
    minute=0,
    id="barrido_recibidas",
    max_instances=1,
    coalesce=True,
    misfire_grace_time=3600,
)

scheduler.add_job(
    job_verificar_recibidas,
    "cron",
    hour=4,
    minute=30,
    id="verificar_recibidas",
    max_instances=1,
    coalesce=True,
    misfire_grace_time=3600,
)
