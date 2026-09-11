import { apiFetch, API_URL } from "./client";

export interface FacturaRecibidaListado {
  id_factura_recibida: number;
  folio_fiscal: string;
  rfc_emisor: string;
  nombre_emisor: string;
  fecha_emision: string;
  monto_total: string;
  sat_estado: string;
  efecto_comprobante: string;
  fecha_cancelacion: string | null;
}

export interface FacturaRecibidaDetalle extends FacturaRecibidaListado {
  rfc_receptor: string;
  nombre_receptor: string;
  rfc_pac: string;
  origen: string;
  sat_es_cancelable: string | null;
  sat_estatus_cancelacion: string | null;
  sat_codigo_estatus: string | null;
  sat_validacion_efos: string | null;
  fecha_ultima_verificacion_sat: string | null;
  intentos_verificacion_fallidos: number;
  id_solicitud: number;
  creado_en: string;
  actualizado_en: string;
}

export interface ResumenFacturasRecibidas {
  total_facturas: number;
  total_monto: string;
  total_vigentes: number;
  total_canceladas: number;
  total_emisores: number;
}

export interface ListadoRecibidasResponse {
  facturas: FacturaRecibidaListado[];
  resumen: ResumenFacturasRecibidas;
  pagina: number;
  por_pagina: number;
  total_paginas: number;
}

export interface EmisorOpcion {
  rfc_emisor: string;
  nombre_emisor: string;
  total_facturas: number;
}

export interface FiltrosRecibidas {
  q?: string;
  rfc_emisor?: string;
  nombre_emisor?: string;
  sat_estado?: "Vigente" | "Cancelado";
  solo_facturas?: boolean;
  efecto_comprobante?: "I" | "E" | "T" | "N" | "P";
  fecha_desde?: string;
  fecha_hasta?: string;
  monto_min?: number;
  monto_max?: number;
  pagina?: number;
  por_pagina?: number;
}

function buildQuery(filtros: FiltrosRecibidas): string {
  const params = new URLSearchParams();
  if (filtros.q) params.set("q", filtros.q);
  if (filtros.rfc_emisor) params.set("rfc_emisor", filtros.rfc_emisor);
  if (filtros.nombre_emisor) params.set("nombre_emisor", filtros.nombre_emisor);
  if (filtros.sat_estado) params.set("sat_estado", filtros.sat_estado);
  if (filtros.solo_facturas !== undefined)
    params.set("solo_facturas", String(filtros.solo_facturas));
  if (filtros.efecto_comprobante)
    params.set("efecto_comprobante", filtros.efecto_comprobante);
  if (filtros.fecha_desde) params.set("fecha_desde", filtros.fecha_desde);
  if (filtros.fecha_hasta) params.set("fecha_hasta", filtros.fecha_hasta);
  if (filtros.monto_min !== undefined) params.set("monto_min", String(filtros.monto_min));
  if (filtros.monto_max !== undefined) params.set("monto_max", String(filtros.monto_max));
  if (filtros.pagina) params.set("pagina", String(filtros.pagina));
  if (filtros.por_pagina) params.set("por_pagina", String(filtros.por_pagina));
  const qs = params.toString();
  return qs ? `?${qs}` : "";
}

export function listarFacturasRecibidas(
  filtros: FiltrosRecibidas = {}
): Promise<ListadoRecibidasResponse> {
  return apiFetch<ListadoRecibidasResponse>(`/facturas-recibidas/${buildQuery(filtros)}`);
}

export function obtenerResumenRecibidas(
  filtros: FiltrosRecibidas = {}
): Promise<ResumenFacturasRecibidas> {
  return apiFetch<ResumenFacturasRecibidas>(
    `/facturas-recibidas/resumen${buildQuery(filtros)}`
  );
}

export function listarEmisores(): Promise<EmisorOpcion[]> {
  return apiFetch<EmisorOpcion[]>("/facturas-recibidas/emisores");
}

export function obtenerFacturaRecibida(id: number): Promise<FacturaRecibidaDetalle> {
  return apiFetch<FacturaRecibidaDetalle>(`/facturas-recibidas/${id}`);
}

export interface SincronizarRequest {
  fecha_inicial: string;
  fecha_final: string;
}

export function sincronizarRecibidas(datos: SincronizarRequest): Promise<unknown> {
  return apiFetch<unknown>("/facturas-recibidas/sincronizar", {
    method: "POST",
    body: JSON.stringify(datos),
  });
}

async function descargarPdf(url: string, nombreArchivo: string) {
  const response = await fetch(url, {
    headers: { "ngrok-skip-browser-warning": "true" },
  });
  if (!response.ok) throw new Error("No se pudo generar el reporte.");
  const blob = await response.blob();
  const objectUrl = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = objectUrl;
  a.download = nombreArchivo;
  a.click();
  URL.revokeObjectURL(objectUrl);
}

export function descargarReporteRecibidas(filtros: FiltrosRecibidas = {}) {
  const { pagina, por_pagina, ...resto } = filtros;
  return descargarPdf(
    `${API_URL}/reportes/recibidas${buildQuery(resto)}`,
    "reporte_facturas_recibidas.pdf"
  );
}