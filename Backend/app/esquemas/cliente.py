from pydantic import BaseModel, ConfigDict, field_validator
from datetime import datetime


class ClienteBase(BaseModel):
    rfc: str
    nombre: str
    dias_plazo_pago: int | None = None

    @field_validator("rfc")
    @classmethod
    def normalizar_rfc(cls, v: str) -> str:
        return v.upper().strip()


class ClienteCrear(ClienteBase):
    pass


class ClienteActualizar(BaseModel):
    nombre: str | None = None
    dias_plazo_pago: int | None = None
    activo: bool | None = None


class ClienteListado(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    rfc: str
    nombre: str
    dias_plazo_pago: int | None
    activo: bool
    fecha_creacion: datetime


class ClienteDetalle(ClienteListado):
    total_facturas: int
    total_ordenes: int