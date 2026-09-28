from ..BaseDeDatos import Base
from sqlalchemy import Column, Date, DateTime, Integer, String, Text


class SincronizacionesPortal(Base):
    """
    Bitacora de corridas contra el portal de CFDI del SAT.

    Una fila por corrida, exitosa o no. De aqui sale lo que ve el usuario
    ("Ultima sincronizacion: hoy 13:02", "No se pudo consultar el SAT desde
    hace 7 horas") y lo que se revisa cuando algo deja de llegar.
    """

    __tablename__ = "SincronizacionesPortal"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)

    tipo_comprobante = Column(String(20), nullable=False, server_default="recibidos")
    fecha_desde = Column(Date, nullable=False)
    fecha_hasta = Column(Date, nullable=False)
    motivo = Column(String(20), nullable=False)       # programada | barrido | manual | cli
    estado = Column(String(20), nullable=False)       # EN_CURSO | EXITOSA | FALLIDA

    inicio = Column(DateTime(timezone=True), nullable=False, index=True)
    fin = Column(DateTime(timezone=True), nullable=True)

    cfdis_encontrados = Column(Integer, nullable=True)
    nuevas = Column(Integer, nullable=True)
    actualizadas = Column(Integer, nullable=True)
    xml_descargados = Column(Integer, nullable=True)
    rechazadas = Column(Integer, nullable=True)

    tipo_error = Column(String(30), nullable=True)
    error = Column(Text, nullable=True)
    avisos = Column(Text, nullable=True)
