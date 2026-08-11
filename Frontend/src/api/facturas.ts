import { apiFetch } from "./client";

// Mirrors the current (example) response of Backend/app/api/rutas/facturas.py
// Nota: el backend aún no expone todos los campos del modelo completo
// (folio_interno, orden_compra, subtotal, iva, total, pdf) — se agregan
// aquí como opcionales para cuando el endpoint se actualice.

export interface Factura {
  id_factura: number;
  folio: string;
  rfc: string;
  cliente: string;
  fecha: string;
  banco: string;
  monto: string;
  descripcion: string;
  id_usuario: number;
  id_estado: number;
  nombre_estado?: string;
  folio_interno?: string;
  orden_compra?: string;
  subtotal?: string;
  iva?: string;
  total?: string;
}

export function listarFacturas(): Promise<Factura[]> {
  return apiFetch<Factura[]>("/facturas");
}