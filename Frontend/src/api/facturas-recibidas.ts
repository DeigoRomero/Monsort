import { apiFetch, descargarArchivo } from "./client";

export interface FacturaRecibidaListado {
  id_factura_recibida: number;
  folio_fiscal: string;
  rfc_emisor: string;
  // null hasta que el SAT se consulta: la metadata del SAT no siempre trae
  // EstadoCFDI y las filas viejas nunca se verificaron. El backend siempre
  // pudo mandar null aqui; el tipo decia string y por eso tsc nunca marco
  // los .toLowerCase() sin guarda que dejaban la pagina en blanco.
  nombre_emisor: string | null;
  fecha_emision: string;
  monto_total: string;
  sat_estado: string | null;
  efecto_comprobante: string | null;
  fecha_cancelacion: string | null;
  tiene_xml: boolean;
}

export interface FacturaRecibidaDetalle {
  id_factura_recibida: number;
  folio_fiscal: string;
  rfc_emisor: string;
  nombre_emisor: string | null;
  rfc_receptor: string;
  nombre_receptor: string | null;
  fecha_emision: string;
  monto_total: string;
  efecto_comprobante: string | null;
  rfc_pac: string | null;
  fecha_cancelacion: string | null;
  origen: string;
  tiene_xml: boolean;
  fecha_vista_portal: string | null;
  sat_estado: string | null;
  sat_es_cancelable: string | null;
  sat_estatus_cancelacion: string | null;
  sat_codigo_estatus: string | null;
  sat_validacion_efos: string | null;
  fecha_ultima_verificacion_sat: string | null;
  intentos_verificacion_fallidos: number;
  id_solicitud: number | null;
  creado_en: string;
  actualizado_en: string | null;
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
  nombre_emisor: string | null;
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

// ---------- Sincronizacion con el portal del SAT ----------

export interface CorridaPortal {
  id: number;
  estado: string; // EN_CURSO | EXITOSA | FALLIDA
  motivo: string; // programada | barrido | manual | cli
  fecha_desde: string;
  fecha_hasta: string;
  inicio: string;
  fin: string | null;
  cfdis_encontrados: number | null;
  nuevas: number | null;
  actualizadas: number | null;
  xml_descargados: number | null;
  rechazadas: number | null;
  tipo_error: string | null;
  error: string | null;
  avisos: string | null;
}

export interface EstadoSincronizacionPortal {
  activo: boolean;
  en_curso: boolean;
  ultima_sincronizacion_exitosa: string | null;
  horas_sin_sincronizar: number | null;
  nuevas_ultimas_24h: number;
  alerta: string | null;
  ultima_corrida: CorridaPortal | null;
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

export function obtenerEstadoSincronizacion(): Promise<EstadoSincronizacionPortal> {
  return apiFetch<EstadoSincronizacionPortal>("/facturas-recibidas/sincronizacion");
}

export function listarHistorialSincronizacion(
  limite = 20
): Promise<CorridaPortal[]> {
  return apiFetch<CorridaPortal[]>(
    `/facturas-recibidas/sincronizacion/historial?limite=${limite}`
  );
}

// Protegida: solo administrador/desarrollador (entra al SAT con la e.firma).
export function iniciarSincronizacionPortal(dias = 5): Promise<unknown> {
  return apiFetch<unknown>("/facturas-recibidas/sincronizacion", {
    method: "POST",
    body: JSON.stringify({ dias }),
  });
}

export function descargarXmlRecibida(id: number) {
  return descargarArchivo(`/facturas-recibidas/${id}/xml`, `recibida_${id}.xml`);
}

export function descargarReporteRecibidas(filtros: FiltrosRecibidas = {}) {
  const { pagina, por_pagina, ...resto } = filtros;
  void pagina;
  void por_pagina;
  return descargarArchivo(
    `/reportes/recibidas${buildQuery(resto)}`,
    "reporte_facturas_recibidas.pdf"
  );
}
