from sqlalchemy import Column, Integer, String, DateTime
from ..BaseDeDatos import Base
from datetime import datetime

class CorreosProcesados(Base):
    __tablename__ = "CorreosProcesados"

    id = Column(Integer, primary_key=True, index=True)
    message_id = Column(String, unique=True, nullable=False)
    tipo_correo = Column(String(30), nullable=True)   # "orden_compra", "factura", "complemento_pago", "desconocido"
    fecha_procesado = Column(DateTime, default=datetime.now)

class CorreosFallidos(Base):
    __tablename__ = "CorreosFallidos"

    id = Column(Integer, primary_key=True, index=True)
    message_id = Column(String, nullable=False)
    error = Column(String, nullable=False)
    fecha_fallo = Column(DateTime, default=datetime.now)
    resuelto = Column(Integer, default=0)   # 0=pendiente, 1=resuelto