from ..BaseDeDatos import Base
from sqlalchemy import (
    Column, Integer, String, DECIMAL, DateTime, ForeignKey, Index, func
)
from sqlalchemy.orm import relationship


class FacturasRecibidas(Base):
    """
    CFDI donde Monsort es RECEPTOR (proveedores le emiten a Monsort).

    Se puebla desde el servicio de Descarga Masiva del SAT con
    TipoSolicitud=Metadata. No confundir con `Facturas`, que son las
    EMITIDAS por Monsort y llegan por Gmail.

    Nota sobre las columnas espejo `sat_*`: los nombres son identicos a los
    de `Facturas` a proposito, aunque el prefijo sea inconsistente
    (`sat_estado` vs `fecha_ultima_verificacion_sat`). Eso es lo que permite
    que el servicio de verificacion SOAP escriba en ambas tablas sin
    bifurcarse. No "arreglar" los nombres aqui.
    """

    __tablename__ = "FacturasRecibidas"

    id_factura_recibida = Column(Integer, primary_key=True, index=True, autoincrement=True)

    # --- Hechos de emision. Propiedad de la ingesta. Nunca se pisan. ---
    folio_fiscal = Column(String(36), unique=True, nullable=False, index=True)
    rfc_emisor = Column(String(13), nullable=False, index=True)
    nombre_emisor = Column(String(254), nullable=True)   # Metadata a veces lo trae vacio
    rfc_receptor = Column(String(13), nullable=False)    # siempre MSF140227BF7
    nombre_receptor = Column(String(254), nullable=True)
    fecha_emision = Column(DateTime(timezone=False), nullable=False, index=True)
    monto_total = Column(DECIMAL(18, 6), nullable=False) # 6 decimales: lo manda el SAT, no truncar
    efecto_comprobante = Column(String(1), nullable=True, index=True)  # I / E / T / N / P
    rfc_pac = Column(String(13), nullable=True)
    origen = Column(String(20), nullable=False, server_default="sat")

    # --- Hechos de estatus. Propiedad de la verificacion (y del barrido
    #     diario de Metadata, que tambien es un mecanismo de verificacion). ---
    fecha_cancelacion = Column(DateTime(timezone=False), nullable=True)

    # Espejo del webservice de consulta de estatus del SAT.
    # Reflejan la ultima respuesta del SAT, no son estado de negocio.
    # Solo se escriben cuando sat_codigo_estatus empieza con "S".
    sat_estado = Column(String(20), nullable=True)
    sat_es_cancelable = Column(String(50), nullable=True)
    sat_estatus_cancelacion = Column(String(50), nullable=True)
    sat_codigo_estatus = Column(String(100), nullable=True)
    sat_validacion_efos = Column(String(10), nullable=True)

    fecha_ultima_verificacion_sat = Column(DateTime(timezone=True), nullable=True)
    intentos_verificacion_fallidos = Column(
        Integer, nullable=False, server_default="0", default=0
    )

    # --- Trazabilidad y auditoria ---
    # nullable: permite altas por el endpoint manual sin inventar una solicitud.
    # La solicitud que la origino lleva tipo_comprobante="recibidos".
    id_solicitud = Column(Integer, ForeignKey("SolicitudesSAT.id"), nullable=True)

    creado_en = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    actualizado_en = Column(DateTime(timezone=True), nullable=True, onupdate=func.now())

    # Relacion nombrada distinto de la columna (ver `cliente_obj` en Facturas)
    solicitud_obj = relationship("SolicitudesSAT", back_populates="facturas_recibidas")

    __table_args__ = (
        # Consulta mas frecuente del cliente: "todo lo de tal proveedor en tal rango"
        Index("ix_facturas_recibidas_emisor_fecha", "rfc_emisor", "fecha_emision"),
    )

    def __repr__(self):
        return (
            f"<FacturasRecibidas {self.folio_fiscal} "
            f"{self.rfc_emisor} {self.monto_total}>"
        )