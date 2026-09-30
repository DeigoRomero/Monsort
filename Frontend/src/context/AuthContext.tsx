import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useState,
  type ReactNode,
} from "react";
import {
  ApiError,
  cerrarSesionServidor,
  establecerAccessToken,
  iniciarSesion,
  refrescarSesion,
  registrarCierreDeSesion,
} from "../api/client";
import type { Usuario } from "../api/auth";

// La sesión ya no se guarda en localStorage (ver api/client.ts). Aquí solo
// vive el usuario; el access token está en memoria dentro de client.ts y el
// refresh token en una cookie HttpOnly que JavaScript no puede leer.
interface Session {
  usuario: Usuario;
}

interface AuthContextValue {
  session: Session | null;
  /** true mientras se intenta recuperar la sesión al abrir la página */
  isRestoring: boolean;
  isLoading: boolean;
  error: string | null;
  signIn: (correo: string, password: string) => Promise<boolean>;
  signOut: () => void;
  clearError: () => void;
}

// Clave vieja: la sesión se guardaba completa (tokens incluidos) aquí.
const CLAVE_VIEJA = "monsort.session";

const AuthContext = createContext<AuthContextValue | undefined>(undefined);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [session, setSession] = useState<Session | null>(null);
  const [isRestoring, setIsRestoring] = useState(true);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    // Limpieza única: borra los tokens que versiones anteriores dejaron en
    // localStorage, donde cualquier script de la página podía leerlos.
    try {
      localStorage.removeItem(CLAVE_VIEJA);
    } catch {
      /* navegador sin storage */
    }

    registrarCierreDeSesion(() => {
      establecerAccessToken(null);
      setSession(null);
      setError("Tu sesión expiró. Vuelve a iniciar sesión.");
    });

    // ¿Hay cookie de sesión válida? Entonces se entra sin pedir contraseña.
    refrescarSesion()
      .then((data) => {
        if (data) setSession({ usuario: data.usuario });
      })
      .finally(() => setIsRestoring(false));
  }, []);

  // Renueva el access token un poco antes de que venza, mientras la pestaña
  // esté abierta. Si la computadora se suspende, el 401 lo resuelve igual.
  useEffect(() => {
    if (!session) return;
    const id = window.setInterval(() => {
      refrescarSesion();
    }, 12 * 60 * 1000);
    return () => window.clearInterval(id);
  }, [session]);

  const signIn = useCallback(async (correo: string, password: string) => {
    setIsLoading(true);
    setError(null);
    try {
      const data = await iniciarSesion(correo, password);
      setSession({ usuario: data.usuario });
      return true;
    } catch (err) {
      if (err instanceof ApiError) {
        setError(err.message);
      } else {
        setError("No se pudo conectar con el servidor. Verifica tu conexión.");
      }
      return false;
    } finally {
      setIsLoading(false);
    }
  }, []);

  const signOut = useCallback(() => {
    cerrarSesionServidor().finally(() => {
      setSession(null);
    });
  }, []);

  const clearError = useCallback(() => setError(null), []);

  return (
    <AuthContext.Provider
      value={{ session, isRestoring, isLoading, error, signIn, signOut, clearError }}
    >
      {children}
    </AuthContext.Provider>
  );
}

// eslint-disable-next-line react-refresh/only-export-components
export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth debe usarse dentro de <AuthProvider>");
  return ctx;
}
