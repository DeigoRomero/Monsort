from sqlalchemy import Column, Integer, String, Date, DateTime, LargeBinary, Numeric, Boolean, ForeignKey
from sqlalchemy.orm import relationship
from ..BaseDeDatos import Base
from datetime import datetime


class ComplementosPago(Base):
    __tablename__ = "ComplementosPago"

    id = Column(Integer, primary_key=True, index=True)
    uuid_cp = Column(String, unique=True, nullable=False)     # folio fiscal del CP
    folio = Column(String, nullable=True)                     # ej. "CP576"

    # nullable: un CP cuyo nodo Pago no se pudo parsear (namespace Pagos10,
    # XML fuera de norma) se guarda igual para revision manual. Antes era
    # NOT NULL y el correo completo se iba al rollback.
    fecha_pago = Column(DateTime, nullable=True)
    moneda = Column(String(10), nullable=True)
    tipo_cambio = Column(Numeric(10, 4), nullable=True)
    monto = Column(Numeric(12, 2), nullable=True)
    forma_pago = Column(String(50), nullable=True)

    archivo_xml = Column(LargeBinary, nullable=True)
    archivo_pdf = Column(LargeBinary, nullable=True)

    # NO unique: un correo puede traer varios CPs. El dedupe es uuid_cp.
    message_id = Column(String, nullable=False, index=True)
    fecha_recepcion = Column(DateTime, default=datetime.now)
    # NO unique: el hash del PDF no identifica al documento fiscal, uuid_cp si.
    hash_archivo = Column(String(64), nullable=True, index=True)

    # Cancelación administrativa
    cancelado = Column(Boolean, nullable=False, default=False)
    fecha_cancelacion = Column(Date, nullable=True)
    motivo_cancelacion = Column(String, nullable=True)
    cancelado_por = Column(Integer, ForeignKey("Usuarios.id_usuario"), nullable=True)

    # Relaciones
    documentos_relacionados = relationship("CPDocumentosRelacionados", back_populates="complemento")
    usuario_cancelacion = relationship("Usuarios", foreign_keys=[cancelado_por])