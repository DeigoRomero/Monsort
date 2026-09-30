from datetime import datetime

from sqlalchemy import Column, DateTime, Integer, String, Text

from app.BaseDeDatos import Base


class Notificaciones(Base):
    """
    Avisos para la pantalla: qué cambió en cada revisión automática.

    Los escribe el scheduler (revisión de Gmail cada 5 minutos, verificación
    SAT) solo cuando HAY cambios; un ciclo sin novedades no genera fila.
    El frontend las consulta con /notificaciones?despues_de=<id>.
    """
    __tablename__ = "Notificaciones"

    id = Column(Integer, primary_key=True, index=True)
    fecha = Column(DateTime, nullable=False, default=datetime.now, index=True)
    tipo = Column(String(20), nullable=False)     # correo | sat | sistema
    nivel = Column(String(10), nullable=False, default="info")   # info | aviso | error
    titulo = Column(String(200), nullable=False)
    detalle = Column(Text, nullable=True)
    # A qué pantalla lleva el aviso: facturas | ordenes | complementos | recibidas
    seccion = Column(String(20), nullable=True)
