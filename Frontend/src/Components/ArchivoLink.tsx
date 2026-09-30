import { useState, type MouseEvent, type ReactNode } from "react";
import { ApiError } from "../api/client";

/**
 * Enlace a un archivo protegido (PDF/XML). Se ve igual que el <a href> de
 * antes, pero pide el archivo con la sesión (fetch + Authorization): un
 * href directo ya no funciona porque la API exige token en todo.
 */
export function ArchivoLink({
  className,
  accion,
  children,
  detenerClic = false,
}: {
  className?: string;
  accion: () => Promise<void>;
  children: ReactNode;
  /** true dentro de filas clicables de una tabla */
  detenerClic?: boolean;
}) {
  const [ocupado, setOcupado] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function manejar(e: MouseEvent) {
    e.preventDefault();
    if (detenerClic) e.stopPropagation();
    if (ocupado) return;
    setOcupado(true);
    setError(null);
    try {
      await accion();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "No se pudo abrir el archivo.");
    } finally {
      setOcupado(false);
    }
  }

  return (
    <>
      <a
        className={className}
        href="#"
        role="button"
        aria-busy={ocupado}
        onClick={manejar}
        style={ocupado ? { opacity: 0.6, cursor: "progress" } : undefined}
      >
        {children}
      </a>
      {error && (
        <span role="alert" style={{ display: "block", color: "#a33b3b", fontSize: 12, marginTop: 4 }}>
          {error}
        </span>
      )}
    </>
  );
}
