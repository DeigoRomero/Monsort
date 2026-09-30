"""
Notificaciones de cambios detectados por los procesos automáticos.

Flujo de la revisión de Gmail (cada 5 minutos, scheduler.job_procesar_correos):

    antes = instantanea(db)          # ids máximos y contadores
    procesar_correos_nuevos(db)      # lo de siempre
    registrar_revision_gmail(db, antes)

registrar_revision_gmail() compara contra la instantánea y, si algo cambió,
guarda UNA notificación con el resumen ("2 facturas nuevas: A1203, A1204 ·
1 orden de compra: NHY13E2871"). Así no hubo que tocar las funciones del
pipeline para que devolvieran qué crearon: la base ya lo sabe.

Siempre guarda la hora de la revisión (configuracion_sistema,
'gmail_ultima_revision') para que la pantalla diga "Gmail revisado hace 2 min"
aunque no haya habido cambios.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.modelos.complemento_pago import ComplementosPago
from app.modelos.configuracion import Configuracion_sistema
from app.modelos.correo_procesado import CorreosFallidos
from app.modelos.factura import Facturas
from app.modelos.notificacion import Notificaciones
from app.modelos.orden_compra import OrdenesCompra

logger = logging.getLogger(__name__)

CLAVE_ULTIMA_REVISION = "gmail_ultima_revision"
MAX_EJEMPLOS = 5
# Los avisos de error (Gmail desconectado) no se repiten más de una vez en
# este lapso: el job corre cada 5 minutos y llenaría la campana.
SILENCIO_ERRORES = timedelta(hours=6)
# Se conservan 90 días; lo viejo se borra solo.
RETENCION = timedelta(days=90)


def _max(db: Session, columna) -> int:
    return db.query(func.coalesce(func.max(columna), 0)).scalar() or 0


def instantanea(db: Session) -> dict:
    return {
        "id_factura": _max(db, Facturas.id_factura),
        "id_cp": _max(db, ComplementosPago.id),
        "id_oc": _max(db, OrdenesCompra.id),
        "id_fallido": _max(db, CorreosFallidos.id),
        "vinculadas_oc": db.query(func.count(Facturas.id_factura))
            .filter(Facturas.id_orden_compra.isnot(None)).scalar() or 0,
        "liquidadas": db.query(func.count(Facturas.id_factura))
            .filter(Facturas.fecha_liquidacion.isnot(None)).scalar() or 0,
    }


def _lista(valores: list[str]) -> str:
    valores = [v for v in valores if v]
    if not valores:
        return ""
    extra = len(valores) - MAX_EJEMPLOS
    texto = ", ".join(valores[:MAX_EJEMPLOS])
    return f"{texto} y {extra} más" if extra > 0 else texto


def _plural(n: int, singular: str, plural: str) -> str:
    return f"{n} {singular if n == 1 else plural}"


def _guardar_ultima_revision(db: Session, momento: datetime) -> None:
    fila = db.query(Configuracion_sistema).filter(
        Configuracion_sistema.clave == CLAVE_ULTIMA_REVISION
    ).first()
    if fila:
        fila.valor = momento.astimezone().isoformat(timespec="seconds")
    else:
        db.add(Configuracion_sistema(
            clave=CLAVE_ULTIMA_REVISION,
            # Con zona horaria: el VPS está en UTC y la pantalla en Tijuana.
            valor=momento.astimezone().isoformat(timespec="seconds"),
        ))


def crear(db: Session, *, tipo: str, titulo: str, detalle: str | None = None,
          nivel: str = "info", seccion: str | None = None,
          evitar_repetidos: bool = False) -> Notificaciones | None:
    if evitar_repetidos:
        reciente = db.query(Notificaciones).filter(
            Notificaciones.tipo == tipo,
            Notificaciones.titulo == titulo[:200],
            Notificaciones.fecha >= datetime.now() - SILENCIO_ERRORES,
        ).first()
        if reciente:
            return None
    n = Notificaciones(tipo=tipo, nivel=nivel, titulo=titulo[:200],
                       detalle=detalle, seccion=seccion, fecha=datetime.now())
    db.add(n)
    return n


def registrar_revision_gmail(db: Session, antes: dict) -> Notificaciones | None:
    """Compara contra la instantánea y deja una notificación si hubo cambios."""
    ahora = datetime.now()
    try:
        facturas = db.query(Facturas.folio_interno, Facturas.folio_fiscal).filter(
            Facturas.id_factura > antes["id_factura"]
        ).order_by(Facturas.id_factura).all()
        cps = db.query(ComplementosPago.folio, ComplementosPago.uuid_cp).filter(
            ComplementosPago.id > antes["id_cp"]
        ).order_by(ComplementosPago.id).all()
        ocs = db.query(OrdenesCompra.numero_oc, OrdenesCompra.confianza_oc).filter(
            OrdenesCompra.id > antes["id_oc"]
        ).order_by(OrdenesCompra.id).all()
        fallidos = db.query(func.count(CorreosFallidos.id)).filter(
            CorreosFallidos.id > antes["id_fallido"]
        ).scalar() or 0
        despues = instantanea(db)
        nuevas_vinculadas = max(0, despues["vinculadas_oc"] - antes["vinculadas_oc"])
        nuevas_liquidadas = max(0, despues["liquidadas"] - antes["liquidadas"])

        partes, detalle = [], []
        seccion = None
        if facturas:
            partes.append(_plural(len(facturas), "factura nueva", "facturas nuevas"))
            detalle.append("Facturas: " + _lista(
                [f.folio_interno or (f.folio_fiscal or "")[:8] for f in facturas]))
            seccion = seccion or "facturas"
        if ocs:
            partes.append(_plural(len(ocs), "orden de compra", "órdenes de compra"))
            revisar = sum(1 for o in ocs if o.confianza_oc in ("baja", "ninguna"))
            texto = "Órdenes de compra: " + _lista([o.numero_oc or "(sin número)" for o in ocs])
            if revisar:
                texto += f" — {_plural(revisar, 'requiere', 'requieren')} revisar el número"
            detalle.append(texto)
            seccion = seccion or "ordenes"
        if cps:
            partes.append(_plural(len(cps), "complemento de pago", "complementos de pago"))
            detalle.append("Complementos: " + _lista(
                [c.folio or (c.uuid_cp or "")[:8] for c in cps]))
            seccion = seccion or "complementos"
        if nuevas_vinculadas:
            partes.append(_plural(nuevas_vinculadas, "factura vinculada a su OC",
                                  "facturas vinculadas a su OC"))
            seccion = seccion or "facturas"
        if nuevas_liquidadas:
            partes.append(_plural(nuevas_liquidadas, "factura liquidada", "facturas liquidadas"))
            seccion = seccion or "facturas"
        if fallidos:
            partes.append(_plural(fallidos, "correo no se pudo procesar",
                                  "correos no se pudieron procesar"))
            detalle.append("Se reintentarán automáticamente cada 10 minutos.")

        _guardar_ultima_revision(db, ahora)

        notificacion = None
        if partes:
            notificacion = crear(
                db, tipo="correo",
                titulo="Revisión de Gmail: " + ", ".join(partes),
                detalle="\n".join(detalle) or None,
                nivel="aviso" if fallidos and not (facturas or ocs or cps) else "info",
                seccion=seccion,
            )
        db.commit()
        return notificacion
    except Exception:
        db.rollback()
        logger.exception("No se pudo registrar la notificación de la revisión de Gmail")
        return None


def registrar_error(db: Session, titulo: str, detalle: str | None = None) -> None:
    """Aviso de error, sin repetirlo más de una vez cada SILENCIO_ERRORES."""
    try:
        crear(db, tipo="sistema", nivel="error", titulo=titulo, detalle=detalle,
              evitar_repetidos=True)
        db.commit()
    except Exception:
        db.rollback()
        logger.exception("No se pudo registrar la notificación de error")


def purgar_viejas(db: Session) -> int:
    try:
        n = db.query(Notificaciones).filter(
            Notificaciones.fecha < datetime.now() - RETENCION
        ).delete(synchronize_session=False)
        db.commit()
        return n
    except Exception:
        db.rollback()
        return 0


def ultima_revision_gmail(db: Session) -> str | None:
    fila = db.query(Configuracion_sistema.valor).filter(
        Configuracion_sistema.clave == CLAVE_ULTIMA_REVISION
    ).first()
    return fila[0] if fila else None
