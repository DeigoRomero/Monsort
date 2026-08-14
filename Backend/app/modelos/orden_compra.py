from sqlalchemy import Column, Integer, String, DateTime, LargeBinary, ForeignKey
from sqlalchemy.orm import relationship
from ..BaseDeDatos import Base
from datetime import datetime

class OrdenesCompra(Base):
    __tablename__ = "ordenes_compra"

    id = Column(Integer, primary_key=True, index=True)
    numero_oc = Column(String, nullable=True)        # nullable: puede no detectarse
    numero_oc_detectado = Column(String, nullable=True)  # raw del parser, sin resolver
    
    archivo = Column(LargeBinary, nullable=True)
    nombre_archivo = Column(String, nullable=True)
    
    message_id = Column(String, unique=True, nullable=False)  # ID de Gmail
    fecha_recepcion = Column(DateTime, default=datetime.now)
    
    capturada_por = Column(Integer, ForeignKey("Usuarios.id_usuario"), nullable=True)
    hash_archivo = Column(String(64), unique=True, nullable=True)
    
    # Relación inversa: facturas asociadas a esta OC
    facturas = relationship("Facturas", back_populates="orden_compra")