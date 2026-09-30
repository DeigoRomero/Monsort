import { useCallback, useEffect, useRef, useState } from "react";

// Tiempos y utilidades de las animaciones (ver Animaciones.tsx). Viven aparte
// para que el recargado en caliente de Vite funcione con los componentes.

export const DURACION_SALIDA_MS = 160;

export function quiereMenosMovimiento(): boolean {
  return typeof window !== "undefined"
    && window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
}

/**
 * Salida animada: primero corre la animación de cierre de la vista actual y
 * DESPUÉS cambia de pantalla. Sin esto React desmonta al instante y no hay
 * nada que animar.
 */
export function useSalida(ms = DURACION_SALIDA_MS) {
  const [saliendo, setSaliendo] = useState(false);
  const temporizador = useRef<number | null>(null);

  useEffect(() => () => {
    if (temporizador.current) window.clearTimeout(temporizador.current);
  }, []);

  const salir = useCallback((despues: () => void) => {
    if (quiereMenosMovimiento()) {
      despues();
      return;
    }
    setSaliendo(true);
    temporizador.current = window.setTimeout(() => {
      setSaliendo(false);
      despues();
    }, ms);
  }, [ms]);

  return { saliendo, salir };
}

