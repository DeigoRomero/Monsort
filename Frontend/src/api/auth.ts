import { apiFetch } from "./client";

// Mirrors Backend/app/esquemas/usuario.py

export interface Usuario {
  id_usuario: number;
  correo: string;
  nombre: string;
  rol: string;
}

export interface LoginResponse {
  access_token: string;
  token_type: string;
  refresh_token: string;
  usuario: Usuario;
}

export interface RefreshResponse {
  access_token: string;
  token_type: string;
}

export function login(correo: string, password: string): Promise<LoginResponse> {
  return apiFetch<LoginResponse>("/auth/login", {
    method: "POST",
    body: JSON.stringify({ correo, password }),
  });
}

export function refreshAccessToken(refresh_token: string): Promise<RefreshResponse> {
  return apiFetch<RefreshResponse>("/auth/refresh", {
    method: "POST",
    body: JSON.stringify({ refresh_token }),
  });
}

export interface RegistroData {
  nombre: string;
  correo: string;
  password: string;
  rol: string;
}

export function registro(data: RegistroData): Promise<Usuario> {
  return apiFetch<Usuario>("/auth/registro", {
    method: "POST",
    body: JSON.stringify(data),
  });
}