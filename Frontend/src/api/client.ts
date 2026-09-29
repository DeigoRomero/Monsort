// Base URL for the FastAPI backend. Configure in a .env file as VITE_API_URL.
// Falls back to the local dev server if not set.
export const API_URL = import.meta.env.VITE_API_URL ?? "http://localhost:8000";

const STORAGE_KEY = "monsort.session";

interface SesionGuardada {
  accessToken: string;
  refreshToken: string;
  usuario: unknown;
}

function leerSesion(): SesionGuardada | null {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    return raw ? (JSON.parse(raw) as SesionGuardada) : null;
  } catch {
    return null;
  }
}

function guardarSesion(sesion: SesionGuardada) {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(sesion));
}

function cerrarSesionForzado() {
  localStorage.removeItem(STORAGE_KEY);
  // apiFetch no vive dentro de un componente de React, asi que no puede usar
  // el AuthContext para cerrar sesion. Recargar hace que App.tsx lea
  // localStorage vacio y vuelva al login limpio.
  window.location.reload();
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
    }
  } catch {
    // response body wasn't JSON — keep the default message
  }
  return detail;
}

let intentandoRefresh: Promise<string | null> | null = null;

/**
 * Pide un access token nuevo con el refresh token guardado.
 * Devuelve el access token nuevo, o null si el refresh tambien fallo.
 */
function refrescarToken(): Promise<string | null> {
  // Si ya hay un refresh en curso, las peticiones que lleguen mientras tanto
  // esperan el mismo resultado en vez de disparar refresh por triplicado.
  if (!intentandoRefresh) {
    intentandoRefresh = (async () => {
      const sesion = leerSesion();
      if (!sesion?.refreshToken) return null;

      try {
        const response = await fetch(`${API_URL}/auth/refresh`, {
          method: "POST",
          headers: {
            "ngrok-skip-browser-warning": "true",
            "Content-Type": "application/json",
          },
          body: JSON.stringify({ refresh_token: sesion.refreshToken }),
        });

        if (!response.ok) return null;

        const data = await response.json();
        guardarSesion({
          ...sesion,
          accessToken: data.access_token,
          refreshToken: data.refresh_token ?? sesion.refreshToken,
        });
        return data.access_token as string;
      } catch {
        return null;
      } finally {
        intentandoRefresh = null;
      }
    })();
  }
  return intentandoRefresh;
}

/**
 * Thin wrapper around fetch for JSON APIs. Throws ApiError on non-2xx
 * responses, using the backend's `detail` message when present.
 *
 * En un 401 intenta refrescar el access token una vez y reintenta la
 * llamada; si el refresh tambien falla, cierra la sesion. Un 403 (rol sin
 * permiso) NO se trata igual: el token es valido, solo falta autorizacion,
 * asi que se deja pasar como ApiError normal para que la pantalla muestre
 * el mensaje sin mandar al usuario al login.
 */
export async function apiFetch<T>(
  path: string,
  options: RequestInit = {},
  _esReintento = false
): Promise<T> {
  const sesion = leerSesion();

  const response = await fetch(`${API_URL}${path}`, {
    ...options,
    headers: {
      "ngrok-skip-browser-warning": "true",
      "Content-Type": "application/json",
      ...(sesion?.accessToken
        ? { Authorization: `Bearer ${sesion.accessToken}` }
        : {}),
      ...options.headers,
    },
  });

  if (response.status === 401 && !_esReintento) {
    const nuevoToken = await refrescarToken();
    if (nuevoToken) {
      return apiFetch<T>(path, options, true);
    }
    cerrarSesionForzado();
    // cerrarSesionForzado recarga la pagina; esta linea casi nunca se lee.
    throw new ApiError(401, "Sesión expirada. Vuelve a iniciar sesión.");
  }

  if (!response.ok) {
    const detail = await extraerDetalle(response);
    throw new ApiError(response.status, detail);
  }

  return response.json() as Promise<T>;
}