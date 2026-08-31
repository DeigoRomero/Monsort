import { apiFetch } from "./client";

export interface Cliente {
  id: number;
  rfc: string;
  nombre: string;
  dias_plazo_pago: number | null;
  activo: boolean;
  fecha_creacion: string;
}

export interface ClienteDetalle extends Cliente {
  total_facturas: number;
  total_ordenes: number;
}

export interface ClienteCrear {
  rfc: string;
  nombre: string;
  dias_plazo_pago?: number | null;
}

export interface ClienteActualizar {
  dias_plazo_pago?: number | null;
  activo?: boolean;
}

export function listarClientes(soloActivos = true): Promise<Cliente[]> {
  return apiFetch<Cliente[]>(`/clientes/?solo_activos=${soloActivos}`);
}

export function obtenerCliente(id: number): Promise<ClienteDetalle> {
  return apiFetch<ClienteDetalle>(`/clientes/${id}`);
}

export function crearCliente(datos: ClienteCrear): Promise<Cliente> {
  return apiFetch<Cliente>("/clientes/", {
    method: "POST",
    body: JSON.stringify(datos),
  });
}

export function actualizarCliente(
  id: number,
  datos: ClienteActualizar
): Promise<Cliente> {
  return apiFetch<Cliente>(`/clientes/${id}`, {
    method: "PATCH",
    body: JSON.stringify(datos),
  });
}