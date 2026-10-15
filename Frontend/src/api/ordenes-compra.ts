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
  id_cliente: number | null;
  cliente: string | null;
  /** Antes de IVA cuando la OC lo desglosa; si no, el total. Decimal como texto. */
  monto: string | null;
  moneda: string | null;
  /** Factura, cotización o reenvío que entró como OC: oculta del listado normal. */
  descartada: boolean;
  motivo_descarte: string | null;
}

export interface OrdenCompraActualizar {
  numero_oc?: string;
  id_cliente?: number | null;
  monto?: string | null;
  moneda?: string | null;
  descartada?: boolean;
}

export interface FiltrosOrdenCompra {
  q?: string;
  id_cliente?: number;
  fecha_desde?: string;
  fecha_hasta?: string;
  monto_min?: string;
  monto_max?: string;
  con_factura?: boolean;
  /** true: solo las descartadas. */
  descartadas?: boolean;
}

export function listarOrdenes(filtros: FiltrosOrdenCompra = {}): Promise<OrdenCompra[]> {
  const params = new URLSearchParams();
  for (const [clave, valor] of Object.entries(filtros)) {
    if (valor !== undefined && valor !== null && valor !== "") {
      params.set(clave, String(valor));
    }
  }
  const qs = params.toString();
  return apiFetch<OrdenCompra[]>(`/ordenes-compra/${qs ? `?${qs}` : ""}`);
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
