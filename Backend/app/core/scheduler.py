import logging
from datetime import timedelta
from zoneinfo import ZoneInfo

from apscheduler.schedulers.background import BackgroundScheduler

from app.BaseDeDatos import SessionLocal
from app.services.factura_service import (
    procesar_correos_nuevos, reprocesar_fallidos, contar_fallidos_pendientes,
)
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


def job_reprocesar_fallidos():
    """
    Drena la cola de CorreosFallidos de a poco.

    Al 26/09/2026 había ~1,700 correos caídos, casi todos por un 403 de cuota
    de Gmail en una llamada que no tenía reintentos. A 40 por ciclo cada 10
    minutos la cola se vacía en unas 7 horas sin volver a saturar la API.
    """
    db = SessionLocal()
    try:
        pendientes = contar_fallidos_pendientes(db)
        if not pendientes:
            return

        logger.info("Reproceso de correos: %d pendiente(s) en cola", pendientes)
        reprocesar_fallidos(db, limite=40)
    except Exception:
        logger.exception("Error en el job de reproceso de correos fallidos")
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


def _sincronizar_portal(dias: int, motivo: str) -> None:
    """
    Corrida contra el portal de CFDI del SAT (ver portal_sat_service).
    No hace nada si SAT_PORTAL_ACTIVO no esta en true: asi se puede
    desplegar el codigo antes de probar la e.firma contra el portal.
    """
    from app.core.config import settings
    from app.services.portal_sat_service import (
        SincronizacionEnCurso, hoy_mexico, limpiar_corridas_colgadas,
        sincronizar_recibidas,
    )

    if not settings.SAT_PORTAL_ACTIVO:
        return

    db = SessionLocal()
    try:
        limpiar_corridas_colgadas(db)
        hoy = hoy_mexico()
        sincronizar_recibidas(db, hoy - timedelta(days=dias), hoy, motivo)
    except SincronizacionEnCurso:
        logger.info("Portal SAT: ya habia una corrida en curso; se omite esta (%s)", motivo)
    except Exception:
        logger.exception("Error en el job del portal SAT (%s)", motivo)
    finally:
        db.close()


def job_portal_reciente():
    """Recibidas de los ultimos dias. Varias veces al dia."""
    from app.services.portal_sat_service import DIAS_VENTANA_RECIENTE, MOTIVO_PROGRAMADA
    _sincronizar_portal(DIAS_VENTANA_RECIENTE, MOTIVO_PROGRAMADA)


def job_portal_barrido():
    """Ultimos 90 dias: refresca cancelaciones tardias. Semanal."""
    from app.services.portal_sat_service import DIAS_BARRIDO, MOTIVO_BARRIDO
    _sincronizar_portal(DIAS_BARRIDO, MOTIVO_BARRIDO)


# Los jobs nuevos llevan zona horaria EXPLICITA. El scheduler global sigue
# en la zona del sistema (UTC en el VPS) para no mover los cron que ya
# existen; ese ajuste esta pendiente aparte.
ZONA_MEXICO = ZoneInfo("America/Mexico_City")

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
    job_reprocesar_fallidos,
    "interval",
    minutes=10,
    id="reprocesar_fallidos",
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

# Portal del SAT: 9, 12, 15 y 18 h, y 22:40 para cerrar el dia (hora de Mexico).
# Minuto 7 para no caer en el :00, cuando mas gente consulta el SAT.
scheduler.add_job(
    job_portal_reciente,
    "cron",
    hour="9,12,15,18",
    minute=7,
    timezone=ZONA_MEXICO,
    id="portal_reciente",
    max_instances=1,
    coalesce=True,
    misfire_grace_time=1800,
)

scheduler.add_job(
    job_portal_reciente,
    "cron",
    hour=22,
    minute=40,
    timezone=ZONA_MEXICO,
    id="portal_cierre_dia",
    max_instances=1,
    coalesce=True,
    misfire_grace_time=1800,
)

scheduler.add_job(
    job_portal_barrido,
    "cron",
    day_of_week="sun",
    hour=3,
    minute=20,
    timezone=ZONA_MEXICO,
    id="portal_barrido",
    max_instances=1,
    coalesce=True,
    misfire_grace_time=3600,
)
