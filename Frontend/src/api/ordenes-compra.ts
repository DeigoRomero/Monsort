import { apiFetch, descargarArchivo } from "./client";

export interface OrdenCompra {
  id: number;
  numero_oc: string | null;
  numero_oc_detectado: string | null;
  nombre_archivo: string | null;
  fecha_recepcion: string;
  tiene_archivo: boolean;
  facturas_asociadas: number;
  sin_factura_30_dias: boolean;
  /** alta | media | baja | ninguna | manual (null: detectada antes del 30/09/2026) */
  confianza_oc: string | null;
}

export interface OrdenCompraActualizar {
  numero_oc?: string | null;
}

export function listarOrdenes(): Promise<OrdenCompra[]> {
  return apiFetch<OrdenCompra[]>("/ordenes-compra/");
}

export function obtenerOrden(idOc: number): Promise<OrdenCompra> {
  return apiFetch<OrdenCompra>(`/ordenes-compra/${idOc}`);
}

export function actualizarOrden(
  idOc: number,
  datos: OrdenCompraActualizar
): Promise<OrdenCompra> {
  return apiFetch<OrdenCompra>(`/ordenes-compra/${idOc}`, {
    method: "PATCH",
    body: JSON.stringify(datos),
  });
}

export function abrirArchivoOC(idOc: number) {
  return descargarArchivo(`/ordenes-compra/${idOc}/archivo`, `OC_${idOc}.pdf`, "abrir");
}