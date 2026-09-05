from ..BaseDeDatos import Base
from sqlalchemy import Column, Integer, String, Date, DateTime, Text


class SolicitudesSAT(Base):
    __tablename__ = "SolicitudesSAT"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)

    # Rango y tipo de lo que se pidió
    fecha_inicial = Column(Date, nullable=False)
    fecha_final = Column(Date, nullable=False)
    tipo_solicitud = Column(String(20), nullable=False)      # metadata | cfdi
    tipo_comprobante = Column(String(20), nullable=False, server_default="emitidos")

    # Máquina de estados propia (no la del SAT)
    estado = Column(String(20), nullable=False, server_default="NUEVA", index=True)

    # Lo que devuelve el SAT
    id_solicitud_sat = Column(String(50), nullable=True, unique=True, index=True)
    estado_solicitud_sat = Column(Integer, nullable=True)    # 1..6
    codigo_estatus = Column(String(10), nullable=True)
    mensaje_sat = Column(String(255), nullable=True)
    numero_cfdis = Column(Integer, nullable=True)

    # Paquetes: JSON serializado como texto
    paquetes = Column(Text, nullable=True)
    paquetes_descargados = Column(Text, nullable=True)

    # Control del polling
    intentos_verificacion = Column(Integer, nullable=False, server_default="0")
    fecha_creacion = Column(DateTime(timezone=True), nullable=False)
    fecha_ultimo_intento = Column(DateTime(timezone=True), nullable=True)
    fecha_completada = Column(DateTime(timezone=True), nullable=True)

    # Resultado de la ingesta
    cfdis_nuevos = Column(Integer, nullable=True)
    cfdis_duplicados = Column(Integer, nullable=True)
    error_ingesta = Column(Text, nullable=True)