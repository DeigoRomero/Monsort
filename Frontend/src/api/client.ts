// Cliente HTTP de la aplicación.
//
// Modelo de sesión (30/09/2026):
//  - El ACCESS TOKEN vive solo en memoria (variable de este módulo). No se
//    guarda en localStorage: cualquier script inyectado en la página (XSS)
//    puede leer localStorage, pero no una variable privada de un módulo.
//  - El REFRESH TOKEN lo guarda el navegador en una cookie HttpOnly que
//    JavaScript no puede leer. Solo viaja a /auth/refresh y /auth/logout.
//  - Al recargar la página no hay access token: se pide uno nuevo con
//    POST /auth/refresh (la cookie va sola) y la sesión sigue.

export const API_URL = import.meta.env.VITE_API_URL ?? "http://localhost:8000";

// Encabezado anti-CSRF que el backend exige en las rutas que usan la cookie.
const CABECERA_CSRF = { "X-Requested-With": "monsort" };

export interface UsuarioSesion {
  id_usuario: number;
  correo: string;
  nombre: string;
  rol: string;
}

export interface RespuestaSesion {
  access_token: string;
  token_type: string;
  expira_en: number;
  usuario: UsuarioSesion;
}

let accessToken: string | null = null;
let alCerrarSesion: (() => void) | null = null;

export function establecerAccessToken(token: string | null) {
  accessToken = token;
}

/** AuthContext registra aquí qué hacer cuando la sesión muere (401 sin remedio). */
export function registrarCierreDeSesion(fn: () => void) {
  alCerrarSesion = fn;
}

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
    this.name = "ApiError";
  }
}

async function extraerDetalle(response: Response): Promise<string> {
  let detail = "Ocurrió un error al comunicarse con el servidor";
  try {
    const data = await response.json();
    if (typeof data?.detail === "string") {
      detail = data.detail;
    } else if (typeof data?.detail?.mensaje === "string") {
      detail = data.detail.mensaje;
    }
  } catch {
    // el cuerpo no era JSON: se queda el mensaje genérico
  }
  if (response.status === 429) {
    detail = detail || "Demasiados intentos. Espera unos minutos.";
  }
  return detail;
}

let refrescoEnCurso: Promise<RespuestaSesion | null> | null = null;

/**
 * Pide un access token nuevo usando la cookie HttpOnly del refresh token.
 * Devuelve la sesión nueva, o null si ya no hay sesión válida.
 * Varias peticiones que fallan a la vez comparten el mismo refresh.
 */
export function refrescarSesion(): Promise<RespuestaSesion | null> {
  if (!refrescoEnCurso) {
    refrescoEnCurso = (async () => {
      try {
        const response = await fetch(`${API_URL}/auth/refresh`, {
          method: "POST",
          credentials: "include",
          headers: { ...CABECERA_CSRF },
        });
        if (!response.ok) return null;
        const data = (await response.json()) as RespuestaSesion;
        accessToken = data.access_token;
        return data;
      } catch {
        return null;
      } finally {
        refrescoEnCurso = null;
      }
    })();
  }
  return refrescoEnCurso;
}

export async function iniciarSesion(correo: string, password: string): Promise<RespuestaSesion> {
  const response = await fetch(`${API_URL}/auth/login`, {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ correo, password }),
  });
  if (!response.ok) {
    throw new ApiError(response.status, await extraerDetalle(response));
  }
  const data = (await response.json()) as RespuestaSesion;
  accessToken = data.access_token;
  return data;
}

export async function cerrarSesionServidor(): Promise<void> {
  try {
    await fetch(`${API_URL}/auth/logout`, {
      method: "POST",
      credentials: "include",
      headers: { ...CABECERA_CSRF },
    });
  } catch {
    // Sin red: igual se borra la sesión local
  } finally {
    accessToken = null;
  }
}

function sesionPerdida() {
  accessToken = null;
  alCerrarSesion?.();
}

/**
 * fetch con el access token. En un 401 intenta refrescar UNA vez y repite;
 * si tampoco se puede, cierra la sesión. Un 403 (sin permiso) no cierra
 * sesión: se muestra como error normal.
 */
async function fetchConSesion(path: string, options: RequestInit = {},
                              esReintento = false): Promise<Response> {
  const headers = new Headers(options.headers);
  if (accessToken) headers.set("Authorization", `Bearer ${accessToken}`);
  if (options.body && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }

  const response = await fetch(`${API_URL}${path}`, { ...options, headers });

  if (response.status === 401 && !esReintento) {
    const sesion = await refrescarSesion();
    if (sesion) return fetchConSesion(path, options, true);
    sesionPerdida();
    throw new ApiError(401, "Tu sesión expiró. Vuelve a iniciar sesión.");
  }
  if (response.status === 401) {
    sesionPerdida();
    throw new ApiError(401, "Tu sesión expiró. Vuelve a iniciar sesión.");
  }
  return response;
}

/** Llamada JSON. Lanza ApiError en respuestas que no son 2xx. */
export async function apiFetch<T>(path: string, options: RequestInit = {}): Promise<T> {
  const response = await fetchConSesion(path, options);
  if (!response.ok) {
    throw new ApiError(response.status, await extraerDetalle(response));
  }
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}

function nombreDesdeEncabezado(response: Response, respaldo: string): string {
  const cd = response.headers.get("Content-Disposition") ?? "";
  const utf8 = /filename\*=UTF-8''([^;]+)/i.exec(cd);
  if (utf8) {
    try {
      return decodeURIComponent(utf8[1]);
    } catch {
      /* se usa el siguiente */
    }
  }
  const simple = /filename="([^"]+)"/i.exec(cd);
  return simple ? simple[1] : respaldo;
}

/**
 * Descarga un archivo protegido (PDF, XML, reportes).
 *
 * Un <a href> no puede mandar el encabezado Authorization, así que ahora
 * que la API exige sesión en todo, los archivos se piden con fetch y se
 * entregan al navegador como blob.
 *  - modo "abrir": PDFs en una pestaña nueva
 *  - modo "descargar": se guarda con su nombre
 */
export async function descargarArchivo(
  path: string,
  respaldo: string,
  modo: "abrir" | "descargar" = "descargar",
): Promise<void> {
  // La pestaña se abre ANTES del await: si se abre después, el navegador
  // la toma como popup no solicitado y la bloquea.
  const pestana = modo === "abrir" ? window.open("", "_blank") : null;
  try {
    const response = await fetchConSesion(path);
    if (!response.ok) {
      throw new ApiError(response.status, await extraerDetalle(response));
    }
    const blob = await response.blob();
    const url = URL.createObjectURL(blob);
    if (pestana) {
      pestana.opener = null;
      pestana.location.href = url;
    } else {
      const a = document.createElement("a");
      a.href = url;
      a.download = nombreDesdeEncabezado(response, respaldo);
      a.rel = "noopener";
      document.body.appendChild(a);
      a.click();
      a.remove();
    }
    // Se libera después: la pestaña nueva necesita la URL mientras carga.
    setTimeout(() => URL.revokeObjectURL(url), 60_000);
  } catch (err) {
    pestana?.close();
    throw err;
  }
}
