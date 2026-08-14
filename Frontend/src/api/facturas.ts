import { apiFetch, API_URL } from "./client";

// Mirrors Backend/app/esquemas/factura.py

export interface FacturaListado {
  id_factura: number;
  folio_fiscal: string;
  folio_interno: string | null;
  cliente: string;
  rfc: string;
  fecha: string;
  numero_oc: string | null;
  total: string | null;
  tipo_cambio: string | null;
  fecha_liquidacion: string | null;
  estado: string;
  tiene_pdf: boolean;
  tiene_xml: boolean;
  tiene_oc: boolean;
  tiene_cp: boolean;
}

export interface ConceptoDetalle {
  descripcion: string | null;
  cantidad: number | null;
  unidad: string | null;
  precio_unitario: number | null;
  importe: number | null;
}

export interface ComplementoResumen {
  id: number;
  folio: string | null;
  fecha_pago: string | null;
  monto: string | null;
  imp_pagado: string | null;
  imp_saldo_insoluto: string | null;
  num_parcialidad: number | null;
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
  subtotal: string | null;
  iva: string | null;
  total: string | null;
  tipo_cambio: string | null;
  fecha_liquidacion: string | null;
  estado: string;
  id_orden_compra: number | null;
  tiene_pdf: boolean;
  tiene_xml: boolean;
  conceptos: ConceptoDetalle[];
  complementos: ComplementoResumen[];
}

export interface FacturaActualizar {
  numero_oc?: string | null;
  folio_interno?: string | null;
}

export interface EstadoOpcion {
  id_estado: number;
  nombre_estado: string;
  descripcion_estado: string;
}

export function listarFacturas(estado?: string): Promise<FacturaListado[]> {
  const query = estado ? `?estado=${encodeURIComponent(estado)}` : "";
  return apiFetch<FacturaListado[]>(`/facturas/${query}`);
}

export function listarEstados(): Promise<EstadoOpcion[]> {
  return apiFetch<EstadoOpcion[]>("/facturas/estados");
}

export function obtenerFactura(idFactura: number): Promise<FacturaDetalle> {
  return apiFetch<FacturaDetalle>(`/facturas/${idFactura}`);
}

export function actualizarFactura(
  idFactura: number,
  datos: FacturaActualizar
): Promise<FacturaDetalle> {
  return apiFetch<FacturaDetalle>(`/facturas/${idFactura}`, {
    method: "PATCH",
    body: JSON.stringify(datos),
  });
}

export function urlPdfFactura(idFactura: number): string {
  return `${API_URL}/facturas/${idFactura}/pdf`;
}