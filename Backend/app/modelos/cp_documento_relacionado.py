from sqlalchemy import Column, Integer, String, Numeric, ForeignKey
from sqlalchemy.orm import relationship
from ..BaseDeDatos import Base

class CPDocumentosRelacionados(Base):
    __tablename__ = "cp_documentos_relacionados"

    id = Column(Integer, primary_key=True, index=True)
    
    id_complemento = Column(Integer, ForeignKey("ComplementosPago.id"), nullable=False)
    uuid_documento = Column(String, nullable=False)          # UUID del XML, siempre presente
    id_factura = Column(Integer, ForeignKey("Facturas.id_factura"), nullable=True)  # lo llena reconciliar()
    
    num_parcialidad = Column(Integer, nullable=True)
    imp_pagado = Column(Numeric(12, 2), nullable=True)
    imp_saldo_insoluto = Column(Numeric(12, 2), nullable=True)
    
    # Relaciones
    complemento = relationship("ComplementosPago", back_populates="documentos_relacionados")
    factura = relationship("Facturas", back_populates="complementos_pago")