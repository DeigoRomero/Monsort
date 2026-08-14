from pydantic import BaseModel, ConfigDict
from datetime import date, datetime
from decimal import Decimal


class FacturaListado(BaseModel):
    """Fila de la tabla principal. Sin binarios, solo banderas."""
    model_config = ConfigDict(from_attributes=True)

    id_factura: int
    folio_fiscal: str
    folio_interno: str | None
    cliente: str
    rfc: str
    fecha: date
    numero_oc: str | None
    total: Decimal | None
    tipo_cambio: str | None
    fecha_liquidacion: date | None
    estado: str
    tiene_pdf: bool
    tiene_xml: bool
    tiene_oc: bool
    tiene_cp: bool


class ConceptoDetalle(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    descripcion: str | None
    cantidad: float | None
    unidad: str | None
    precio_unitario: float | None
    importe: float | None


class ComplementoResumen(BaseModel):
    """CP asociado a la factura, para la vista de detalle."""
    model_config = ConfigDict(from_attributes=True)

    id: int
    folio: str | None
    fecha_pago: datetime | None
    monto: Decimal | None
    imp_pagado: Decimal | None
    imp_saldo_insoluto: Decimal | None
    num_parcialidad: int | None


class FacturaDetalle(BaseModel):
    """Vista completa de una factura."""
    model_config = ConfigDict(from_attributes=True)

    id_factura: int
    folio_fiscal: str
    folio_interno: str | None
    cliente: str
    rfc: str
    fecha: date
    numero_oc: str | None
    numero_oc_detectado: str | None
    subtotal: Decimal | None
    iva: Decimal | None
    total: Decimal | None
    tipo_cambio: str | None
    fecha_liquidacion: date | None
    estado: str
    id_orden_compra: int | None
    tiene_pdf: bool
    tiene_xml: bool
    conceptos: list[ConceptoDetalle]
    complementos: list[ComplementoResumen]


class FacturaActualizar(BaseModel):
    """Campos que el empleado puede corregir desde el dashboard."""
    numero_oc: str | None = None
    folio_interno: str | None = None