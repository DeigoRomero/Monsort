from pydantic import BaseModel, ConfigDict
from datetime import date, datetime
from decimal import Decimal


# ---------- PIEZAS ----------

class FacturaDelComplemento(BaseModel):
    """La factura a la que un DoctoRelacionado del CP quedó pegado."""
    model_config = ConfigDict(from_attributes=True)

    id_factura: int | None = None
    folio_fiscal: str
    folio_interno: str | None = None
    cliente: str | None = None
    total: Decimal | None = None
    estado: str | None = None
    vinculada: bool           # False = el UUID viene en el CP pero no se pudo pegar


class DocumentoRelacionado(BaseModel):
    """Un DoctoRelacionado del XML del CP."""
    model_config = ConfigDict(from_attributes=True)

    uuid_documento: str
    num_parcialidad: int | None = None
    imp_pagado: Decimal | None = None
    imp_saldo_insoluto: Decimal | None = None
    liquida: bool             # imp_saldo_insoluto == 0
    factura: FacturaDelComplemento | None = None


# ---------- LISTADO ----------

class ComplementoListado(BaseModel):
    """Fila de la tabla de CPs. Sin binarios, solo banderas."""
    model_config = ConfigDict(from_attributes=True)

    id: int
    uuid_cp: str
    folio: str | None = None
    fecha_pago: datetime | None = None
    fecha_recepcion: datetime | None = None
    monto: Decimal | None = None
    moneda: str | None = None
    tipo_cambio: Decimal | None = None
    forma_pago: str | None = None
    cancelado: bool = False
    tiene_pdf: bool = False
    tiene_xml: bool = False

    documentos_total: int = 0
    documentos_vinculados: int = 0
    documentos_huerfanos: int = 0
    facturas: list[str] = []          # folio_interno o UUID corto, para la tabla
    # Señales de captura incompleta que el cliente debe poder ver de un golpe
    requiere_atencion: bool = False
    motivo_atencion: str | None = None


class ResumenComplementos(BaseModel):
    total_complementos: int
    total_monto: Decimal
    con_pdf: int
    sin_pdf: int
    totalmente_vinculados: int
    con_huerfanos: int
    cancelados: int
    sin_fecha_pago: int


class ComplementoListadoConResumen(BaseModel):
    complementos: list[ComplementoListado]
    resumen: ResumenComplementos
    pagina: int
    por_pagina: int
    total_paginas: int


# ---------- DETALLE ----------

class ComplementoDetalle(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    uuid_cp: str
    folio: str | None = None
    fecha_pago: datetime | None = None
    fecha_recepcion: datetime | None = None
    monto: Decimal | None = None
    moneda: str | None = None
    tipo_cambio: Decimal | None = None
    forma_pago: str | None = None
    message_id: str | None = None
    tiene_pdf: bool = False
    tiene_xml: bool = False

    cancelado: bool = False
    fecha_cancelacion: date | None = None
    motivo_cancelacion: str | None = None

    documentos: list[DocumentoRelacionado] = []
    requiere_atencion: bool = False
    motivo_atencion: str | None = None


# ---------- FILTROS ----------

class FiltrosComplemento(BaseModel):
    """Se usa con Depends() en el endpoint de listado."""

    q: str | None = None                    # uuid_cp, folio, o UUID de factura referida
    forma_pago: str | None = None
    vinculado: bool | None = None           # True = todos sus documentos pegados
    incluir_cancelados: bool = False
    solo_atencion: bool = False             # sin PDF, sin fecha de pago, o con huérfanos

    fecha_desde: date | None = None         # sobre fecha_pago
    fecha_hasta: date | None = None

    pagina: int = 1
    por_pagina: int = 50


# ---------- HUÉRFANOS ----------

class DocumentoHuerfano(BaseModel):
    """
    Un DoctoRelacionado del CP cuyo UUID no quedó pegado a ninguna factura.

    Es la respuesta directa a "no tenemos forma de saber si están correctos":
    aquí sale el pago que el cliente cobró contra una factura que el sistema
    no tiene, o que tiene en un estado que reconciliar() ignora.
    """
    id_complemento: int
    uuid_cp: str
    folio_cp: str | None = None
    fecha_pago: datetime | None = None
    uuid_documento: str
    imp_pagado: Decimal | None = None
    imp_saldo_insoluto: Decimal | None = None
    factura_existe: bool
    estado_factura: str | None = None
    motivo: str


# ---------- DIAGNÓSTICO ----------

class ConteoPorTipo(BaseModel):
    tipo_correo: str | None = None
    correos: int
    ultimo: datetime | None = None


class CorreoFallido(BaseModel):
    id: int
    message_id: str
    error: str
    fecha_fallo: datetime | None = None
    intentos: int = 0
    resuelto: int = 0
    reintentable: bool = True


class DiagnosticoCorreos(BaseModel):
    """Lo que antes había que sacar a mano con psql en el VPS."""

    complementos_guardados: int
    complementos_cancelados: int
    complementos_sin_pdf: int
    complementos_sin_fecha_pago: int
    ultimo_complemento: datetime | None = None

    documentos_cp_total: int
    documentos_cp_vinculados: int
    documentos_cp_huerfanos: int

    correos_procesados: int
    ultimo_correo_procesado: datetime | None = None
    correos_por_tipo: list[ConteoPorTipo] = []

    correos_fallidos_pendientes: int
    correos_fallidos_reintentables: int
    correos_fallidos_resueltos: int
    ultimo_fallo: datetime | None = None
    errores_frecuentes: list[dict] = []


class ReporteReproceso(BaseModel):
    intentados: int = 0
    recuperados: int = 0
    ya_estaban: int = 0
    siguen_fallando: int = 0
    documentos: dict = {}
    pendientes_restantes: int = 0
    detalle: list[dict] = []
    error: str | None = None
