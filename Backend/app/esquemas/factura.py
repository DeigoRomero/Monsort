from pydantic import BaseModel, ConfigDict
from datetime import date, datetime
from decimal import Decimal
from app.esquemas.orden_compra import OrdenCompraResumen

from app.esquemas.orden_compra import OrdenCompraListado


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
    tipo_cambio: Decimal | None = None
    moneda: str | None = None
    fecha_validacion: date | None
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
    tipo_cambio: Decimal | None = None
    fecha_liquidacion: date | None
    fecha_validacion: date | None
    moneda: str | None = None
    estado: str
    orden_compra: OrdenCompraResumen | None
    tiene_pdf: bool
    tiene_xml: bool
    conceptos: list[ConceptoDetalle]
    complementos: list[ComplementoResumen]


class FacturaActualizar(BaseModel):
    """Campos que el empleado puede corregir desde el dashboard."""
    numero_oc: str | None = None
    folio_interno: str | None = None
    fecha_validacion: date | None = None    
    tipo_cambio: Decimal | None = None  

class VincularOCRequest(BaseModel):
    """Cuerpo de la peticion para vincular manualmente una factura a una OC especifica."""
    id_orden_compra: int
    forzar: bool = False

    # ---------- FILTROS (query builder) ----------

class FiltrosFactura(BaseModel):
    """
    Parámetros de búsqueda y filtrado. Se usa con Depends() en los endpoints.

    Uso en router:
        @router.get("/")
        def listar(filtros: FiltrosFactura = Depends(), db: Session = Depends(get_db)):
    """
    # Búsqueda libre (OR sobre los 4 campos)
    q: str | None = None                        # cliente, UUID, folio_interno, numero_oc

    # Filtros exactos
    cliente: str | None = None                  # ilike
    numero_oc: str | None = None                # ilike
    con_cp: bool | None = None                  # True = tiene CP vinculado, False = sin CP
    incluir_canceladas: bool = False            # por defecto las excluye

    # Rango de fechas (sobre Facturas.fecha = fecha_emision del CFDI)
    fecha_desde: date | None = None
    fecha_hasta: date | None = None

    # Paginación (solo para listado, ignorado en resumen y reportes)
    pagina: int = 1
    por_pagina: int = 50


# ---------- RESUMEN (cálculos anidados) ----------

class ResumenFacturas(BaseModel):
    """
    Totales calculados sobre el mismo filtro del listado.
    Se devuelve junto con el listado en un único response.
    """
    total_facturas: int
    total_mxn: Decimal                  # suma de totales convertidos a MXN
    total_con_cp: int                   # facturas que tienen CP vinculado
    total_sin_cp: int
    total_canceladas: int               # solo si incluir_canceladas=True, sino 0

# ---------- CANCELACIÓN ----------

class CancelarRequest(BaseModel):
    motivo: str | None = None
    fecha_validacion: date | None = None


# ---------- LISTADO CON RESUMEN ----------

class FacturaListadoConResumen(BaseModel):
    """Response del endpoint principal de listado."""
    facturas: list  # list[FacturaListado] — importar desde el mismo módulo
    resumen: ResumenFacturas
    pagina: int
    por_pagina: int
    total_paginas: int