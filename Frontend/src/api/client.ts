// Base URL for the FastAPI backend. Configure in a .env file as VITE_API_URL.
// Falls back to the local dev server if not set.
export const API_URL = import.meta.env.VITE_API_URL ?? "http://localhost:8000";

const STORAGE_KEY = "monsort.session";

function obtenerAccessToken(): string | null {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return null;
    const session = JSON.parse(raw) as { accessToken?: string };
    return session.accessToken ?? null;
  } catch {
    return null;
  }
}

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
    this.name = "ApiError";
  }
}

/**
 * Thin wrapper around fetch for JSON APIs. Throws ApiError on non-2xx
 * responses, using the backend's `detail` message when present.
 */
export async function apiFetch<T>(
  path: string,
  options: RequestInit = {}
): Promise<T> {
  const token = obtenerAccessToken();

  const response = await fetch(`${API_URL}${path}`, {
    ...options,
    headers: {
      "ngrok-skip-browser-warning": "true",
      "Content-Type": "application/json",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...options.headers,
    },
  });

  if (!response.ok) {
    let detail = "Ocurrió un error al comunicarse con el servidor";
    try {
      const data = await response.json();
      if (typeof data?.detail === "string") {
        detail = data.detail;
      }
    } catch {
      // response body wasn't JSON — keep the default message
    }
    throw new ApiError(response.status, detail);
  }

  return response.json() as Promise<T>;
}