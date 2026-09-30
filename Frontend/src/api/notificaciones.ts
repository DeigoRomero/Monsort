import { apiFetch } from "./client";

// Espejo de Backend/app/api/rutas/notificaciones.py
export interface Notificacion {
  id: number;
  fecha: string;
  tipo: "correo" | "sat" | "sistema" | string;
  nivel: "info" | "aviso" | "error" | string;
  titulo: string;
  detalle: string | null;
  /** facturas | ordenes | complementos | recibidas */
  seccion: string | null;
}

export interface RespuestaNotificaciones {
  ultima_revision_gmail: string | null;
  ultimo_id: number;
  notificaciones: Notificacion[];
}

export function listarNotificaciones(despuesDe = 0, limite = 30): Promise<RespuestaNotificaciones> {
  return apiFetch<RespuestaNotificaciones>(
    `/notificaciones/?despues_de=${despuesDe}&limite=${limite}`
  );
}
