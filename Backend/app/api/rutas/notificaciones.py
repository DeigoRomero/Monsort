"""
GET /notificaciones — avisos de las revisiones automáticas.

El frontend consulta cada minuto con ?despues_de=<último id que ya vio> y
muestra un aviso emergente por cada notificación nueva, más la campana con
el historial. "Leída" se lleva en el navegador de cada usuario (último id
visto): no hace falta una tabla por usuario para 3-4 personas.
"""
from datetime import datetime

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.BaseDeDatos import get_db
from app.modelos.notificacion import Notificaciones
from app.services.notificacion_service import ultima_revision_gmail

router = APIRouter()


class NotificacionSalida(BaseModel):
    id: int
    fecha: datetime
    tipo: str
    nivel: str
    titulo: str
    detalle: str | None
    seccion: str | None


class RespuestaNotificaciones(BaseModel):
    ultima_revision_gmail: str | None
    ultimo_id: int
    notificaciones: list[NotificacionSalida]


@router.get("/", response_model=RespuestaNotificaciones)
def listar_notificaciones(
    despues_de: int = Query(0, ge=0, description="Solo las posteriores a este id"),
    limite: int = Query(30, ge=1, le=100),
    db: Session = Depends(get_db),
):
    consulta = db.query(Notificaciones)
    if despues_de:
        consulta = consulta.filter(Notificaciones.id > despues_de)
    filas = consulta.order_by(Notificaciones.id.desc()).limit(limite).all()

    ultimo = db.query(Notificaciones.id).order_by(Notificaciones.id.desc()).first()
    return RespuestaNotificaciones(
        ultima_revision_gmail=ultima_revision_gmail(db),
        ultimo_id=ultimo[0] if ultimo else 0,
        notificaciones=[
            NotificacionSalida(
                id=n.id,
                # Guardada en hora del servidor (UTC en el VPS): se manda con
                # zona para que el navegador la muestre en hora de Tijuana.
                fecha=n.fecha.astimezone() if n.fecha.tzinfo is None else n.fecha,
                tipo=n.tipo, nivel=n.nivel, titulo=n.titulo,
                detalle=n.detalle, seccion=n.seccion,
            )
            for n in filas
        ],
    )
