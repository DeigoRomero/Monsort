from pydantic import BaseModel, ConfigDict
from datetime import datetime, date


class OrdenCompraListado(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    numero_oc: str | None
    numero_oc_detectado: str | None
    nombre_archivo: str | None
    fecha_recepcion: datetime | None
    tiene_archivo: bool
    facturas_asociadas: int
    sin_factura_30_dias: bool = False


class OrdenCompraActualizar(BaseModel):
    numero_oc: str

class OrdenCompraResumen(BaseModel):
    """OC anidada dentro del detalle de una factura. Sin el conteo de facturas asociadas."""
    model_config = ConfigDict(from_attributes=True)

    id: int
    numero_oc: str | None
    numero_oc_detectado: str | None
    nombre_archivo: str | None
    fecha_recepcion: datetime | None
    tiene_archivo: bool