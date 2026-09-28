from pydantic import BaseModel, ConfigDict
from datetime import date, datetime
from decimal import Decimal


# ---------- LISTADO ----------

class FacturaRecibidaListado(BaseModel):
    """
    Fila de la tabla. Exactamente los seis campos que pidio el cliente:
    folio fiscal, RFC, nombre de quien emitio, fecha, monto y estatus.

    Las demas columnas espejo (sat_codigo_estatus, sat_validacion_efos...)
    van solo en el detalle: mandarlas todas al listado obligaria al
    frontend a decidir cuales ignorar, y esa decision es del backend.
    """
    model_config = ConfigDict(from_attributes=True)

    id_factura_recibida: int
    folio_fiscal: str
    rfc_emisor: str
    nombre_emisor: str | None
    fecha_emision: datetime
    monto_total: Decimal
    sat_estado: str | None                  # "Vigente" | "Cancelado"
    efecto_comprobante: str | None          # I | E | T | N | P
    fecha_cancelacion: datetime | None
    tiene_xml: bool = False                 # habilita el boton "Descargar XML"


# ---------- DETALLE ----------

class FacturaRecibidaDetalle(BaseModel):
    """Vista completa, incluidas las columnas espejo del SAT."""
    model_config = ConfigDict(from_attributes=True)

    id_factura_recibida: int
    folio_fiscal: str
    rfc_emisor: str
    nombre_emisor: str | None
    rfc_receptor: str
    nombre_receptor: str | None
    fecha_emision: datetime
    monto_total: Decimal
    efecto_comprobante: str | None
    rfc_pac: str | None
    fecha_cancelacion: datetime | None
    origen: str                             # sat | excel | portal
    tiene_xml: bool = False
    fecha_vista_portal: datetime | None = None

    # Espejo del webservice de consulta de estatus del SAT
    sat_estado: str | None
    sat_es_cancelable: str | None
    sat_estatus_cancelacion: str | None
    sat_codigo_estatus: str | None
    sat_validacion_efos: str | None
    fecha_ultima_verificacion_sat: datetime | None
    intentos_verificacion_fallidos: int

    id_solicitud: int | None
    creado_en: datetime
    actualizado_en: datetime | None


# ---------- FILTROS ----------

class FiltrosFacturaRecibida(BaseModel):
    """
    Parametros de busqueda. Se usa con Depends() en los endpoints.

    A diferencia de FiltrosFactura, las canceladas SI se incluyen por
    defecto: el cliente pidio explicitamente ver el estatus, y ocultarlas
    haria invisible justo lo que quiere vigilar.
    """
    # Busqueda libre (OR sobre folio_fiscal, rfc_emisor, nombre_emisor)
    q: str | None = None

    # Filtros exactos
    rfc_emisor: str | None = None               # ilike
    nombre_emisor: str | None = None            # ilike
    sat_estado: str | None = None               # "Vigente" | "Cancelado"

    # Por defecto solo comprobantes de ingreso: los complementos de pago
    # y notas de credito de los proveedores ensucian la vista del cliente.
    solo_facturas: bool = True
    efecto_comprobante: str | None = None       # si se especifica, gana sobre solo_facturas

    # Rango de fechas sobre fecha_emision
    fecha_desde: date | None = None
    fecha_hasta: date | None = None             # INCLUSIVO, ver query_builder

    # Rango de monto
    monto_min: Decimal | None = None
    monto_max: Decimal | None = None

    # Paginacion (ignorada en resumen y reportes)
    pagina: int = 1
    por_pagina: int = 50


# ---------- RESUMEN ----------

class ResumenFacturasRecibidas(BaseModel):
    """Totales calculados en SQL sobre el mismo filtro del listado."""
    total_facturas: int
    total_monto: Decimal
    total_vigentes: int
    total_canceladas: int
    total_emisores: int


# ---------- LISTADO CON RESUMEN ----------

class FacturaRecibidaListadoConResumen(BaseModel):
    """Response del endpoint principal."""
    facturas: list[FacturaRecibidaListado]
    resumen: ResumenFacturasRecibidas
    pagina: int
    por_pagina: int
    total_paginas: int


# ---------- EMISORES (dropdown) ----------

class EmisorResumen(BaseModel):
    """Entrada del dropdown de proveedores."""
    rfc_emisor: str
    nombre_emisor: str | None
    total_facturas: int


# ---------- SINCRONIZACION MANUAL ----------

class SincronizarRequest(BaseModel):
    """Dispara una solicitud de descarga para un rango concreto."""
    fecha_inicial: date
    fecha_final: date


# ---------- SINCRONIZACION CON EL PORTAL DEL SAT ----------

class CorridaPortal(BaseModel):
    """Una fila de SincronizacionesPortal."""
    id: int
    estado: str                             # EN_CURSO | EXITOSA | FALLIDA
    motivo: str                             # programada | barrido | manual | cli
    fecha_desde: date
    fecha_hasta: date
    inicio: datetime
    fin: datetime | None
    cfdis_encontrados: int | None
    nuevas: int | None
    actualizadas: int | None
    xml_descargados: int | None
    rechazadas: int | None
    tipo_error: str | None
    error: str | None
    avisos: str | None


class EstadoSincronizacionPortal(BaseModel):
    """
    Lo que va arriba de la tabla de Recibidas:
      "Ultima sincronizacion con el SAT: hoy 13:02 · 7 nuevas en 24 h"
    y, si `alerta` trae texto, un aviso visible tal cual.
    """
    activo: bool
    en_curso: bool
    ultima_sincronizacion_exitosa: datetime | None
    horas_sin_sincronizar: float | None
    nuevas_ultimas_24h: int
    alerta: str | None
    ultima_corrida: CorridaPortal | None


class SincronizarPortalRequest(BaseModel):
    """Sincronizacion manual. Por defecto, los ultimos 5 dias."""
    dias: int = 5

