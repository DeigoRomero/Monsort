import { apiFetch, descargarArchivo } from "./client";

export interface FacturaListado {
  id_factura: number;
  folio_fiscal: string;
  folio_interno: string | null;
  cliente: string;
  rfc: string;
  fecha: string;
  numero_oc: string | null;
  total: number | null;
  moneda: string | null;
  tipo_cambio: number | null;
  fecha_liquidacion: string | null;
  fecha_validacion: string | null;
  estado: string;
  tiene_pdf: boolean;
  tiene_xml: boolean;
  tiene_oc: boolean;
  tiene_cp: boolean;
  subtotal: number | string | null;
  iva: number | string | null;
  fecha_limite_pago: string | null;
  alerta_vencimiento: "vigente" | "por_vencer" | "vencida" | "pagada" | null;
  /** Días hasta la fecha límite de pago; negativo = vencida hace N días. */
  dias_restantes: number | null;
}

export interface ResumenFacturas {
  total_facturas: number;
  /** Total facturado: suma del IMPORTE (subtotal, sin IVA) en MXN. */
  importe_mxn: number;
  importe_mxn_historico: number;
  /** Suma de totales con IVA en MXN (referencia). */
  total_mxn: number;
  total_con_cp: number;
  total_sin_cp: number;
  total_canceladas: number;
  total_historico: number;
  total_mxn_historico: number;
}

export interface ListadoFacturasResponse {
  facturas: FacturaListado[];
  resumen: ResumenFacturas;
  pagina: number;
  por_pagina: number;
  total_paginas: number;
}

export interface FiltrosFacturas {
  q?: string;
  cliente?: string;
  numero_oc?: string;
  estado?: string;
  con_cp?: boolean;
  incluir_canceladas?: boolean;
  incluir_historico?: boolean;
  origen?: string;
  fecha_desde?: string;
  fecha_hasta?: string;
  pagina?: number;
  por_pagina?: number;
}

export interface EstadoOpcion {
  id_estado: number;
  nombre_estado: string;
  descripcion_estado: string;
}

export interface OrdenCompraInfo {
  id: number;
  numero_oc: string;
  numero_oc_detectado: string | null;
  nombre_archivo: string | null;
  fecha_recepcion: string;
  tiene_archivo: boolean;
}

export interface OrdenCompraCandidata {
  id: number;
  numero_oc: string;
  numero_oc_detectado: string | null;
  nombre_archivo: string | null;
  fecha_recepcion: string;
  tiene_archivo: boolean;
  facturas_asociadas: number;
  confianza_oc?: string | null;
}

export interface ComplementoResumen {
  id: number;
  uuid_cp: string;
  folio: string | null;
  fecha_pago: string | null;
  monto: number | null;
  imp_pagado: number | null;
  imp_saldo_insoluto: number | null;
  num_parcialidad: number | null;
  liquida: boolean;
}

export interface FacturaDetalle {
  id_factura: number;
  folio_fiscal: string;
  folio_interno: string | null;
  cliente: string;
  rfc: string;
  fecha: string;
  numero_oc: string | null;
  numero_oc_detectado: string | null;
  subtotal: number | null;
  iva: number | null;
  total: number | null;
  tipo_cambio: number | null;
  fecha_liquidacion: string | null;
  fecha_validacion: string | null;
  moneda: string | null;
  estado: string;
  orden_compra: OrdenCompraInfo | null;
  tiene_pdf: boolean;
  tiene_xml: boolean;
  complementos: ComplementoResumen[];
}

export interface FacturaActualizar {
  numero_oc?: string | null;
  folio_interno?: string | null;
  fecha_validacion?: string | null;
}

function buildQuery(filtros: FiltrosFacturas): string {
  const params = new URLSearchParams();
  if (filtros.q) params.set("q", filtros.q);
  if (filtros.cliente) params.set("cliente", filtros.cliente);
  if (filtros.numero_oc) params.set("numero_oc", filtros.numero_oc);
  if (filtros.estado) params.set("estado", filtros.estado);
  if (filtros.con_cp !== undefined) params.set("con_cp", String(filtros.con_cp));
  if (filtros.incluir_canceladas !== undefined)
    params.set("incluir_canceladas", String(filtros.incluir_canceladas));
  if (filtros.incluir_historico !== undefined)
    params.set("incluir_historico", String(filtros.incluir_historico));
  if (filtros.origen) params.set("origen", filtros.origen);
  if (filtros.fecha_desde) params.set("fecha_desde", filtros.fecha_desde);
  if (filtros.fecha_hasta) params.set("fecha_hasta", filtros.fecha_hasta);
  if (filtros.pagina) params.set("pagina", String(filtros.pagina));
  if (filtros.por_pagina) params.set("por_pagina", String(filtros.por_pagina));
  const qs = params.toString();
  return qs ? `?${qs}` : "";
}

export function listarFacturas(
  filtros: FiltrosFacturas = {}
): Promise<ListadoFacturasResponse> {
  return apiFetch<ListadoFacturasResponse>(`/facturas/${buildQuery(filtros)}`);
}

export function obtenerResumen(
  filtros: FiltrosFacturas = {}
): Promise<ResumenFacturas> {
  return apiFetch<ResumenFacturas>(`/facturas/resumen${buildQuery(filtros)}`);
}

export function listarEstados(): Promise<EstadoOpcion[]> {
  return apiFetch<EstadoOpcion[]>("/facturas/estados");
}

export function obtenerFactura(idFactura: number): Promise<FacturaDetalle> {
  return apiFetch<FacturaDetalle>(`/facturas/${idFactura}`);
}

export function listarOcsCandidatas(idFactura: number): Promise<OrdenCompraCandidata[]> {
  return apiFetch<OrdenCompraCandidata[]>(`/facturas/${idFactura}/ocs-candidatas`);
}

export function vincularOc(
  idFactura: number,
  idOrdenCompra: number,
  forzar = false
): Promise<FacturaDetalle> {
  return apiFetch<FacturaDetalle>(`/facturas/${idFactura}/vincular-oc`, {
    method: "POST",
    body: JSON.stringify({ id_orden_compra: idOrdenCompra, forzar }),
  });
}

export function actualizarFactura(
  idFactura: number,
  datos: FacturaActualizar
): Promise<FacturaListado> {
  return apiFetch<FacturaListado>(`/facturas/${idFactura}`, {
    method: "PATCH",
    body: JSON.stringify(datos),
  });
}

export function cancelarFactura(
  idFactura: number,
  motivo?: string
): Promise<void> {
  return apiFetch<void>(`/facturas/${idFactura}/cancelar`, {
    method: "PATCH",
    body: JSON.stringify({ motivo }),
  });
}

export function cancelarCP(idCp: number, motivo?: string): Promise<void> {
  return apiFetch<void>(`/facturas/cp/${idCp}/cancelar`, {
    method: "PATCH",
    body: JSON.stringify({ motivo }),
  });
}

export function abrirPdfFactura(idFactura: number) {
  return descargarArchivo(`/facturas/${idFactura}/pdf`, `factura_${idFactura}.pdf`, "abrir");
}

export function descargarXmlFactura(idFactura: number) {
  return descargarArchivo(`/facturas/${idFactura}/xml`, `factura_${idFactura}.xml`);
}

export function descargarReporteGeneral(filtros: FiltrosFacturas = {}) {
  const { pagina, por_pagina, ...resto } = filtros;
  void pagina;
  void por_pagina;
  return descargarArchivo(`/reportes/general${buildQuery(resto)}`, "reporte_general.pdf");
}

export function descargarReporteDetalle(idFactura: number) {
  return descargarArchivo(`/reportes/detalle/${idFactura}`, `reporte_factura_${idFactura}.pdf`);
}

export interface VerificacionSat {
  id_factura: number;
  folio_fiscal: string;
  resultado: string;
  cambio_aplicado: boolean;
  id_estado_anterior: number;
  id_estado_actual: number;
  sat_estado: string;
  sat_es_cancelable: string;
  sat_estatus_cancelacion: string;
  sat_codigo_estatus: string;
  sat_validacion_efos: string;
  fecha_verificacion: string;
}

export function verificarSat(idFactura: number): Promise<VerificacionSat> {
  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), 40000);

  return apiFetch<VerificacionSat>(`/facturas/${idFactura}/verificar-sat`, {
    method: "POST",
    signal: controller.signal,
  }).finally(() => clearTimeout(timeoutId));
}

