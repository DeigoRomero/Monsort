from sqlalchemy import Column, Integer, String, DateTime
from ..BaseDeDatos import Base
from datetime import datetime

class CorreosProcesados(Base):
    __tablename__ = "CorreosProcesados"

    id = Column(Integer, primary_key=True, index=True)
    message_id = Column(String, unique=True, nullable=False)
    # 120 y no 30: describir_resumen() genera cadenas como
    # "facturas:1+complementos:1+ordenes:1" (35 caracteres) y el insert ocurre
    # DESPUES de guardar los documentos, asi que el desborde se llevaba todo
    # al rollback.
    tipo_correo = Column(String(120), nullable=True)
    fecha_procesado = Column(DateTime, default=datetime.now)

class CorreosFallidos(Base):
    __tablename__ = "CorreosFallidos"

    id = Column(Integer, primary_key=True, index=True)
    message_id = Column(String, nullable=False, index=True)
    error = Column(String, nullable=False)
    fecha_fallo = Column(DateTime, default=datetime.now)
    resuelto = Column(Integer, default=0, index=True)   # 0=pendiente, 1=resuelto
    # Cuantas veces se intento procesar este correo. El job de reproceso
    # deja de insistir al llegar a MAX_INTENTOS_REPROCESO.
    intentos = Column(Integer, nullable=False, default=0, server_default="0")
