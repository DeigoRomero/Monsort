from sqlalchemy import (
    Column, Integer, String, Boolean, DateTime, ForeignKey, func
)
from sqlalchemy.orm import relationship

from ..BaseDeDatos import Base


class Cliente(Base):
    __tablename__ = "Clientes"

    id = Column(Integer, primary_key=True, index=True)
    rfc = Column(String(13), unique=True, nullable=False, index=True)
    nombre = Column(String(255), nullable=False)
    dias_plazo_pago = Column(Integer, nullable=True)
    activo = Column(Boolean, nullable=False, default=True)

    fecha_creacion = Column(DateTime, nullable=False, server_default=func.now())
    fecha_actualizacion = Column(DateTime, onupdate=func.now())

    correos = relationship(
        "ClienteCorreo",
        back_populates="cliente",
        cascade="all, delete-orphan",
    )
    facturas = relationship("Facturas", back_populates="cliente_obj")


class ClienteCorreo(Base):
    __tablename__ = "ClientesCorreos"

    id = Column(Integer, primary_key=True, index=True)
    id_cliente = Column(
        Integer, ForeignKey("Clientes.id"), nullable=False, index=True
    )
    # Guarda dominio ("cliente.com") o correo completo ("compras@cliente.com")
    valor = Column(String(255), unique=True, nullable=False, index=True)
    es_dominio = Column(Boolean, nullable=False, default=True)

    cliente = relationship("Cliente", back_populates="correos")