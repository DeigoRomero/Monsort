from ..BaseDeDatos import Base
from sqlalchemy import Column, Integer, String
from sqlalchemy.orm import relationship

class Estados(Base):
    __tablename__ = "Estados"
    id_estado = Column(Integer, primary_key=True, index=True, autoincrement=True)
    nombre_estado = Column(String, nullable=False)
    descripcion_estado = Column(String, nullable=False)
    facturas = relationship("Facturas", back_populates="estado")
    historial_verificacion = relationship("HistorialVerificacion", back_populates="estado")