import { apiFetch } from "./client";

// Mirrors Backend/app/modelos/factura.py (clase Facturas)
// Nota: el endpoint GET /facturas actualmente regresa el objeto de
// SQLAlchemy directo (sin esquema Pydantic), así que algunos campos
// podrían no serializarse bien todavía (relaciones, binarios).
// Los campos abajo son opcionales para que el frontend no truene si
// alguno falta en la respuesta.

export interface Estado {
  id_estado: number;
  nombre_estado: string;
  descripcion_estado?: string;
}

export interface Factura {
  id_factura: number;
  folio_fiscal: string;
  rfc: string;
  cliente: string;
  fecha: string;
  folio_interno: string;
  orden_compra: string;
  tipo_cambio?: string;
  subtotal?: string;
  iva?: string;
  total?: string;
  id_usuario: number;
  id_estado: number;
  estado?: Estado;
}

export function listarFacturas(): Promise<Factura[]> {
  return apiFetch<Factura[]>("/facturas");
}