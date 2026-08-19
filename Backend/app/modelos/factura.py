from ..BaseDeDatos import Base
from sqlalchemy import Column, Integer, String, Date, DECIMAL, ForeignKey, LargeBinary, Boolean, text
from sqlalchemy.orm import relationship


class Facturas(Base):
    __tablename__ = "Facturas"

    id_factura = Column(Integer, primary_key=True, index=True, autoincrement=True)
    message_id = Column(String, unique=True, nullable=True)
    folio_fiscal = Column(String, nullable=False)
    rfc = Column(String, nullable=False)
    cliente = Column(String, nullable=False)
    fecha = Column(Date, nullable=False, index=True)          # índice agregado en migración
    folio_interno = Column(String, nullable=True)
    numero_oc = Column(String, nullable=True)
    moneda = Column(String(3), nullable=False, server_default="MXN")
    tipo_cambio = Column(DECIMAL(18, 6), nullable=True)       # era String, ya es Numeric en DB
    subtotal = Column(DECIMAL(10, 2), nullable=True)
    iva = Column(DECIMAL(10, 2), nullable=True)
    total = Column(DECIMAL(10, 2), nullable=True)
    pdf_factura = Column(LargeBinary, nullable=True)
    xml_factura = Column(LargeBinary, nullable=True)
    orden_compra_archivo = Column(LargeBinary, nullable=True)
    fecha_validacion = Column(Date, nullable=True)            # captura manual en dashboard
    fecha_liquidacion = Column(Date, nullable=True)
    numero_oc_detectado = Column(String, nullable=True)       # raw del parser

    # Foreign keys
    id_usuario = Column(Integer, ForeignKey("Usuarios.id_usuario"), nullable=False)
    id_estado = Column(Integer, ForeignKey("Estados.id_estado"), nullable=False)
    id_orden_compra = Column(Integer, ForeignKey("ordenes_compra.id"), nullable=True)

    # Relaciones
    conceptos = relationship("Conceptos", back_populates="factura")
    usuario = relationship("Usuarios", back_populates="facturas")
    estado = relationship("Estados", back_populates="facturas")
    historial_verificacion = relationship("HistorialVerificacion", back_populates="factura")
    orden_compra = relationship("OrdenesCompra", back_populates="facturas")
    complementos_pago = relationship("CPDocumentosRelacionados", back_populates="factura")


class HistorialVerificacion(Base):
    __tablename__ = "HistorialVerificacion"

    id_historial = Column(Integer, primary_key=True, index=True)
    id_factura = Column(Integer, ForeignKey("Facturas.id_factura"), nullable=False)
    id_estado = Column(Integer, ForeignKey("Estados.id_estado"), nullable=False)
    id_usuario = Column(Integer, ForeignKey("Usuarios.id_usuario"), nullable=False)
    fecha_verificacion = Column(Date, nullable=False)
    resultado_verificacion = Column(String, nullable=False)
    origen = Column(String(20), nullable=False, server_default="manual")  # "manual" | "sat"

    # Relaciones
    factura = relationship("Facturas", back_populates="historial_verificacion")
    estado = relationship("Estados", back_populates="historial_verificacion")
    usuario = relationship("Usuarios", back_populates="historial_verificacion")