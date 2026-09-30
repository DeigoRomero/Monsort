from datetime import datetime

from pydantic import BaseModel, ConfigDict, field_validator


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


class OrdenCompraActualizar(BaseModel):
    numero_oc: str

    @field_validator("numero_oc")
    @classmethod
    def _validar(cls, v: str) -> str:
        v = (v or "").strip()
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
