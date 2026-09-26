import { apiFetch, API_URL } from "./client";

// Mirrors Backend/app/esquemas/complemento.py

export interface FacturaDelComplemento {
  id_factura: number | null;
  folio_fiscal: string;
  folio_interno: string | null;
  cliente: string | null;
  total: string | null;
  estado: string | null;
  vinculada: boolean;
}

export interface DocumentoRelacionado {
  uuid_documento: string;
  num_parcialidad: number | null;
  imp_pagado: string | null;
  imp_saldo_insoluto: string | null;
  liquida: boolean;
  factura: FacturaDelComplemento | null;
}

export interface ComplementoListado {
  id: number;
  uuid_cp: string;
  folio: string | null;
  fecha_pago: string | null;
  fecha_recepcion: string | null;
  monto: string | null;
  moneda: string | null;
  tipo_cambio: string | null;
  forma_pago: string | null;
  cancelado: boolean;
  tiene_pdf: boolean;
  tiene_xml: boolean;
  documentos_total: number;
  documentos_vinculados: number;
  documentos_huerfanos: number;
  facturas: string[];
  requiere_atencion: boolean;
  motivo_atencion: string | null;
}

export interface ResumenComplementos {
  total_complementos: number;
  total_monto: string;
  con_pdf: number;
  sin_pdf: number;
  totalmente_vinculados: number;
  con_huerfanos: number;
  cancelados: number;
  sin_fecha_pago: number;
}

export interface ComplementoListadoConResumen {
  complementos: ComplementoListado[];
  resumen: ResumenComplementos;
  pagina: number;
  por_pagina: number;
  total_paginas: number;
}

export interface ComplementoDetalle {
  id: number;
  uuid_cp: string;
  folio: string | null;
  fecha_pago: string | null;
  fecha_recepcion: string | null;
  monto: string | null;
  moneda: string | null;
  tipo_cambio: string | null;
  forma_pago: string | null;
  message_id: string | null;
  tiene_pdf: boolean;
  tiene_xml: boolean;
  cancelado: boolean;
  fecha_cancelacion: string | null;
  motivo_cancelacion: string | null;
  documentos: DocumentoRelacionado[];
  requiere_atencion: boolean;
  motivo_atencion: string | null;
}

export interface DocumentoHuerfano {
  id_complemento: number;
  uuid_cp: string;
  folio_cp: string | null;
  fecha_pago: string | null;
  uuid_documento: string;
  imp_pagado: string | null;
  imp_saldo_insoluto: string | null;
  factura_existe: boolean;
  estado_factura: string | null;
  motivo: string;
}

export interface FiltrosComplemento {
  q?: string;
  forma_pago?: string;
  vinculado?: boolean;
  incluir_cancelados?: boolean;
  solo_atencion?: boolean;
  fecha_desde?: string;
  fecha_hasta?: string;
  pagina?: number;
  por_pagina?: number;
}

function buildQuery(filtros: FiltrosComplemento): string {
  const params = new URLSearchParams();
  if (filtros.q) params.set("q", filtros.q);
  if (filtros.forma_pago) params.set("forma_pago", filtros.forma_pago);
  if (filtros.vinculado !== undefined)
    params.set("vinculado", String(filtros.vinculado));
  if (filtros.incluir_cancelados !== undefined)
    params.set("incluir_cancelados", String(filtros.incluir_cancelados));
  if (filtros.solo_atencion !== undefined)
    params.set("solo_atencion", String(filtros.solo_atencion));
  if (filtros.fecha_desde) params.set("fecha_desde", filtros.fecha_desde);
  if (filtros.fecha_hasta) params.set("fecha_hasta", filtros.fecha_hasta);
  if (filtros.pagina) params.set("pagina", String(filtros.pagina));
  if (filtros.por_pagina) params.set("por_pagina", String(filtros.por_pagina));
  const qs = params.toString();
  return qs ? `?${qs}` : "";
}

export function listarComplementos(
  filtros: FiltrosComplemento = {}
): Promise<ComplementoListadoConResumen> {
  return apiFetch<ComplementoListadoConResumen>(
    `/complementos/${buildQuery(filtros)}`
  );
}

export function obtenerResumenComplementos(
  filtros: FiltrosComplemento = {}
): Promise<ResumenComplementos> {
  return apiFetch<ResumenComplementos>(
    `/complementos/resumen${buildQuery(filtros)}`
  );
}

export function listarHuerfanos(): Promise<DocumentoHuerfano[]> {
  return apiFetch<DocumentoHuerfano[]>("/complementos/huerfanos");
}

export function obtenerComplemento(id: number): Promise<ComplementoDetalle> {
  return apiFetch<ComplementoDetalle>(`/complementos/${id}`);
}

export function reconciliarComplementos(): Promise<unknown> {
  return apiFetch<unknown>("/complementos/reconciliar", { method: "POST" });
}

export function urlPdfComplemento(id: number): string {
  return `${API_URL}/complementos/${id}/pdf`;
}

export function urlXmlComplemento(id: number): string {
  return `${API_URL}/complementos/${id}/xml`;
}