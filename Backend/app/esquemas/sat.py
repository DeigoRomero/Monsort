from pydantic import BaseModel, ConfigDict
from datetime import datetime
from decimal import Decimal


class CoberturaMes(BaseModel):
    """Una fila del reporte de cobertura, por mes."""
    model_config = ConfigDict(from_attributes=True)

    mes: str                      # AAAA-MM
    sat_dice: int                 # comprobantes en la metadata del SAT
    tenemos: int                  # filas en Facturas con fecha en ese mes
    faltantes: int                # en la metadata y ausentes de Facturas
    # Sin metadata de ese mes, `faltantes` no significa nada: no es que no
    # falte, es que no se ha consultado.
    metadata_consultada: bool


class ComprobanteFaltante(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    folio_fiscal: str
    fecha_emision: datetime | None = None
    monto_total: Decimal | None = None
    rfc_receptor: str | None = None
    nombre_receptor: str | None = None
    efecto_comprobante: str | None = None
    sat_estado: str | None = None
    mes: str | None = None        # AAAA-MM, para armar la solicitud cfdi


class BusquedaUuid(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    folio_fiscal: str
    en_facturas: bool
    en_metadata_sat: bool
    pagos_que_la_referencian: int

    fecha_emision_sat: datetime | None = None
    monto_sat: Decimal | None = None
    receptor_sat: str | None = None
    estado_sat: str | None = None
    mes_sugerido: str | None = None

    recomendacion: str
