import { apiFetch, type UsuarioSesion } from "./client";

// Espejo de Backend/app/esquemas/usuario.py
export type Usuario = UsuarioSesion;

export interface RegistroData {
  nombre: string;
  correo: string;
  password: string;
  rol: string;
}

/** Solo administrador/desarrollador (el backend lo exige). */
export function registro(data: RegistroData): Promise<Usuario> {
  return apiFetch<Usuario>("/auth/registro", {
    method: "POST",
    body: JSON.stringify(data),
  });
}

export function cambiarPassword(actual: string, nueva: string): Promise<void> {
  return apiFetch<void>("/auth/cambiar-password", {
    method: "POST",
    body: JSON.stringify({ actual, nueva }),
  });
}

/** Enlace de Google para (re)conectar el buzón de Gmail. Solo administradores. */
export function enlaceGmail(): Promise<{ url: string; vigencia_horas: number }> {
  return apiFetch("/auth/gmail/enlace", { method: "POST" });
}

/** Reglas de contraseña del backend (app/core/seguridad.py::validar_password). */
export function problemaPassword(password: string, correo = ""): string | null {
  if (password.length < 12) return "Mínimo 12 caracteres.";
  if (new TextEncoder().encode(password).length > 72) return "Máximo 72 bytes.";
  const clases = [/[a-z]/, /[A-Z]/, /\d/, /[^A-Za-z0-9]/].filter((r) => r.test(password)).length;
  if (clases < 3) return "Usa al menos tres de: minúsculas, mayúsculas, números y símbolos.";
  const usuario = correo.split("@")[0]?.toLowerCase();
  if (usuario && password.toLowerCase().includes(usuario)) return "No debe contener el correo.";
  return null;
}
