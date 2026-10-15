from sqlalchemy import Column, Integer, Numeric, String, DateTime, LargeBinary, ForeignKey
from sqlalchemy.orm import relationship
from ..BaseDeDatos import Base
from datetime import datetime

class OrdenesCompra(Base):
    __tablename__ = "ordenes_compra"

    id = Column(Integer, primary_key=True, index=True)
    numero_oc = Column(String, nullable=True)        # nullable: puede no detectarse
    numero_oc_detectado = Column(String, nullable=True)  # raw del parser, sin resolver
    # alta | media | baja | ninguna — qué tan seguro estuvo detector_oc.
    # 'baja'/'ninguna' se marcan en pantalla para que alguien lo revise.
    confianza_oc = Column(String(10), nullable=True)
    
    archivo = Column(LargeBinary, nullable=True)
    nombre_archivo = Column(String, nullable=True)
    
    # NO unique: un correo puede traer varias OCs sueltas. El dedupe es
    # hash_archivo, que si sigue siendo unique mas abajo.
    message_id = Column(String, nullable=False, index=True)  # ID de Gmail
    fecha_recepcion = Column(DateTime, default=datetime.now)
    
    capturada_por = Column(Integer, ForeignKey("Usuarios.id_usuario"), nullable=True)
    hash_archivo = Column(String(64), unique=True, nullable=True)

    id_cliente = Column(Integer, ForeignKey("Clientes.id"), nullable=True, index=True)

    # Monto de la OC ANTES de IVA cuando el documento lo desglosa (si no, el
    # total). Lo llena detector_oc.detectar_monto_oc; editable a mano.
    monto = Column(Numeric(14, 2), nullable=True)
    moneda = Column(String(3), nullable=True)            # MXN | USD
    origen = Column(String(20 ), nullable=False, server_default="gmail")
    
    # Relación inversa: facturas asociadas a esta OC
    facturas = relationship("Facturas", back_populates="orden_compra")