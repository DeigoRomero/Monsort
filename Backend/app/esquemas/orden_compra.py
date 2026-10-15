from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class OrdenCompraListado(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    numero_oc: str | None
    numero_oc_detectado: str | None
    # alta | media | baja | ninguna | manual (None en OCs anteriores al detector)
    confianza_oc: str | None = None
    nombre_archivo: str | None
    fecha_recepcion: datetime | None
    tiene_archivo: bool
    facturas_asociadas: int
    sin_factura_30_dias: bool = False
    id_cliente: int | None = None
    cliente: str | None = None
    # Antes de IVA cuando la OC lo desglosa; si no, el total del documento.
    monto: Decimal | None = None
    moneda: str | None = None


class FiltrosOrdenCompra(BaseModel):
    """Filtros de GET /ordenes-compra/ (todos opcionales y combinables)."""
    q: str | None = Field(None, max_length=100)
    id_cliente: int | None = None
    fecha_desde: date | None = None
    fecha_hasta: date | None = None
    monto_min: Decimal | None = Field(None, ge=0)
    monto_max: Decimal | None = Field(None, ge=0)
    con_factura: bool | None = None
    sin_numero: bool | None = None


class OrdenCompraActualizar(BaseModel):
    """PATCH parcial: solo se cambia lo que venga en el cuerpo."""
    numero_oc: str | None = None
    id_cliente: int | None = None
    monto: Decimal | None = Field(None, ge=0, max_digits=14, decimal_places=2)
    moneda: str | None = None

    @model_validator(mode="after")
    def _algo_que_cambiar(self):
        if not self.model_fields_set:
            raise ValueError("No hay cambios que guardar")
        return self

    @field_validator("moneda")
    @classmethod
    def _validar_moneda(cls, v: str | None) -> str | None:
        if v is None:
            return v
        v = v.strip().upper()
        if v not in ("MXN", "USD"):
            raise ValueError("La moneda debe ser MXN o USD")
        return v

    @field_validator("numero_oc")
    @classmethod
    def _validar(cls, v: str | None) -> str | None:
        if v is None:
            raise ValueError("El número de OC no puede ir vacío")
        v = v.strip()
        if not v:
            raise ValueError("El número de OC no puede ir vacío")
        if len(v) > 50:
            raise ValueError("El número de OC es demasiado largo (máx. 50)")
        if any(ord(c) < 32 for c in v):
            raise ValueError("El número de OC tiene caracteres no válidos")
        return v


class OrdenCompraResumen(BaseModel):
    """OC anidada dentro del detalle de una factura. Sin el conteo de facturas asociadas."""
    model_config = ConfigDict(from_attributes=True)

    id: int
    numero_oc: str | None
    numero_oc_detectado: str | None
    nombre_archivo: str | None
    fecha_recepcion: datetime | None
    tiene_archivo: bool
