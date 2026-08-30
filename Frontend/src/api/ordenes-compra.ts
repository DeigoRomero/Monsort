import { apiFetch, API_URL } from "./client";

export interface OrdenCompra {
  id: number;
  numero_oc: string;
  numero_oc_detectado: string | null;
  nombre_archivo: string | null;
  fecha_recepcion: string;
  tiene_archivo: boolean;
  facturas_asociadas: number;
  sin_factura_30_dias: boolean;
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

export function urlArchivoOC(idOc: number): string {
  return `${API_URL}/ordenes-compra/${idOc}/archivo`;
}