import { apiFetch, API_URL } from "./client";

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
}

export interface ResumenFacturas {
  total_facturas: number;
  total_mxn: number;
  total_con_cp: number;
  total_sin_cp: number;
  total_canceladas: number;
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

function buildQuery(filtros: FiltrosFacturas): string {
  const params = new URLSearchParams();
  if (filtros.q) params.set("q", filtros.q);
  if (filtros.cliente) params.set("cliente", filtros.cliente);
  if (filtros.numero_oc) params.set("numero_oc", filtros.numero_oc);
  if (filtros.estado) params.set("estado", filtros.estado);
  if (filtros.con_cp !== undefined) params.set("con_cp", String(filtros.con_cp));
  if (filtros.incluir_canceladas !== undefined)
    params.set("incluir_canceladas", String(filtros.incluir_canceladas));
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

export interface FacturaActualizar {
  numero_oc?: string | null;
  folio_interno?: string | null;
  fecha_validacion?: string | null;
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

export function urlPdfFactura(idFactura: number): string {
  return `${API_URL}/facturas/${idFactura}/pdf`;
}

// ---------- Reportes PDF ----------

async function descargarPdf(url: string, nombreArchivo: string) {
  const response = await fetch(url, {
    headers: { "ngrok-skip-browser-warning": "true" },
  });
  if (!response.ok) {
    throw new Error("No se pudo generar el reporte.");
  }
  const blob = await response.blob();
  const objectUrl = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = objectUrl;
  a.download = nombreArchivo;
  a.click();
  URL.revokeObjectURL(objectUrl);
}

export function descargarReporteGeneral(filtros: FiltrosFacturas = {}) {
  return descargarPdf(
    `${API_URL}/reportes/general${buildQuery(filtros)}`,
    "reporte_general.pdf"
  );
}

export function descargarReporteDetalle(idFactura: number) {
  return descargarPdf(
    `${API_URL}/reportes/detalle/${idFactura}`,
    `reporte_factura_${idFactura}.pdf`
  );
}