from ..BaseDeDatos import Base
from sqlalchemy import Column, Integer, String, DECIMAL, ForeignKey
from sqlalchemy.orm import relationship

class Conceptos(Base):
    __tablename__ = "Conceptos"
    id_concepto = Column(Integer, primary_key=True, index=True)
    id_factura = Column(Integer, ForeignKey("Facturas.id_factura"), nullable=False)
    descripcion = Column(String, nullable=False)
    cantidad = Column(DECIMAL(10, 2), nullable=False)
    unidad = Column(String, nullable=True)          # ej. "Pieza", "Kg", "Servicio"
    precio_unitario = Column(DECIMAL(10, 2), nullable=True)
    importe = Column(DECIMAL(10, 2), nullable=True)  # cantidad * precio_unitario, normalmente ya viene en el XML
    factura = relationship("Facturas", back_populates="conceptos")