from ..BaseDeDatos import Base
from sqlalchemy import (
    Column, Integer, String, DECIMAL, DateTime, ForeignKey, Index, func
)


class MetadataEmitidas(Base):
    """
    Lo que el SAT dice que Monsort EMITIO. No es una factura: es un indice.

    Por que existe: `ingerir_metadata()` descargaba el paquete de metadata de
    emitidas, contaba los UUID contra `Facturas` y tiraba el archivo. Ese TXT
    trae la FechaEmision de cada comprobante, que es justo el dato que hacia
    falta el 26/09/2026 para saber de que mes eran 10 facturas cuyos pagos
    (CP) existian sin su factura. Se descartaba en cada corrida.

    Guardarlo cambia el orden de las cosas: la metadata es el indice BARATO
    (su solicitud no consume el limite de por vida) y el tipo 'cfdi' es la
    copia CARA. Con el indice se localiza el mes exacto y luego se gasta una
    sola solicitud precisa, en lugar de adivinar el rango.

    De regalo queda la reconciliacion permanente: "el SAT dice que emitiste
    112 en agosto y tienes 105". Esa comparacion habria cazado este incidente
    el primer dia en vez de a las dos semanas.

    NO confundir con:
      Facturas            las emitidas de verdad, con XML, PDF y OC
      FacturasRecibidas   CFDI donde Monsort es receptor
      MetadataEmitidas    solo el dicho del SAT sobre las emitidas
    """

    __tablename__ = "MetadataEmitidas"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)

    # --- Hechos de emision. Propiedad de la ingesta, nunca se pisan. ---
    folio_fiscal = Column(String(36), unique=True, nullable=False, index=True)
    rfc_emisor = Column(String(13), nullable=False)      # siempre MSF140227BF7
    nombre_emisor = Column(String(254), nullable=True)
    rfc_receptor = Column(String(13), nullable=False, index=True)
    nombre_receptor = Column(String(254), nullable=True)
    fecha_emision = Column(DateTime(timezone=False), nullable=False, index=True)
    monto_total = Column(DECIMAL(18, 6), nullable=False)  # 6 decimales: los manda el SAT
    efecto_comprobante = Column(String(1), nullable=True, index=True)  # I/E/T/N/P
    rfc_pac = Column(String(13), nullable=True)

    # --- Hechos de estatus. El barrido de metadata los refresca. ---
    sat_estado = Column(String(20), nullable=True)        # 'Vigente' | 'Cancelado'
    fecha_cancelacion = Column(DateTime(timezone=False), nullable=True)

    # --- Trazabilidad ---
    id_solicitud = Column(Integer, ForeignKey("SolicitudesSAT.id"), nullable=True)
    ingerido_en = Column(DateTime(timezone=True), server_default=func.now())
    actualizado_en = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        # La consulta que importa: cobertura por mes y faltantes por mes.
        Index("ix_metadata_emitidas_fecha_estado", "fecha_emision", "sat_estado"),
    )
