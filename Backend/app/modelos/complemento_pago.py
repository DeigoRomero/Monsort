from sqlalchemy import Column, Integer, String, DateTime, LargeBinary, Numeric
from sqlalchemy.orm import relationship
from ..BaseDeDatos import Base
from datetime import datetime

class ComplementosPago(Base):
    __tablename__ = "ComplementosPago"

    id = Column(Integer, primary_key=True, index=True)
    uuid_cp = Column(String, unique=True, nullable=False)   # folio fiscal del CP
    folio = Column(String, nullable=True)                    # ej. "CP576"
    
    fecha_pago = Column(DateTime, nullable=False)
    moneda = Column(String(10), nullable=True)
    tipo_cambio = Column(Numeric(10, 4), nullable=True)
    monto = Column(Numeric(12, 2), nullable=True)
    forma_pago = Column(String(50), nullable=True)
    
    archivo_xml = Column(LargeBinary, nullable=True)
    archivo_pdf = Column(LargeBinary, nullable=True)
    
    message_id = Column(String, unique=True, nullable=False)
    fecha_recepcion = Column(DateTime, default=datetime.now)
    hash_archivo = Column(String(64), unique=True, nullable=True)
    
    # Relación con documentos relacionados
    documentos_relacionados = relationship("CPDocumentosRelacionados", back_populates="complemento")