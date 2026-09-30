import type { ReactNode } from "react";
import "./Animaciones.css";

// Animaciones de la interfaz (30/09/2026). Sobrias a propósito: movimientos
// cortos (150-220 ms) que ayudan a seguir qué se abrió o cerró, sin
// distraer. Con "reducir movimiento" activado en el sistema operativo todo
// pasa al instante (ver index.css y movimiento.ts).

/**
 * Contenido que se abre y cierra deslizándose.
 *
 * Usa la transición de `grid-template-rows` de 0fr a 1fr: el navegador
 * anima la altura real del contenido sin tener que medirla en JavaScript.
 * El contenido se queda montado aunque esté cerrado (los filtros conservan
 * lo que el usuario escribió) y se marca `inert` para que el teclado no
 * pueda llegar a campos que no se ven.
 */
export function Plegable({
  abierto,
  id,
  children,
  className = "",
}: {
  abierto: boolean;
  id?: string;
  children: ReactNode;
  className?: string;
}) {
  return (
    <div
      id={id}
      className={`plegable${abierto ? " plegable-abierto" : ""} ${className}`}
      inert={!abierto}
      aria-hidden={!abierto}
    >
      <div className="plegable-interior">{children}</div>
    </div>
  );
}

/** Botón "Filtros" con contador de filtros activos y flecha que gira. */
export function BotonFiltros({
  abierto,
  activos,
  onClick,
  controla,
}: {
  abierto: boolean;
  activos: number;
  onClick: () => void;
  controla: string;
}) {
  return (
    <button
      type="button"
      className={`boton-filtros${abierto ? " boton-filtros-abierto" : ""}`}
      onClick={onClick}
      aria-expanded={abierto}
      aria-controls={controla}
    >
      <span>Filtros</span>
      {activos > 0 && <span className="boton-filtros-contador">{activos}</span>}
      <Flecha abierta={abierto} />
    </button>
  );
}

export function Flecha({ abierta }: { abierta: boolean }) {
  return (
    <svg
      className={`flecha${abierta ? " flecha-abierta" : ""}`}
      width="12"
      height="12"
      viewBox="0 0 12 12"
      aria-hidden="true"
    >
      <path d="M3 4.5 6 7.5 9 4.5" fill="none" stroke="currentColor" strokeWidth="1.5"
        strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

/**
 * Envoltura de una pantalla completa (listado o detalle).
 *   "detalle": entra desde la derecha y sale hacia la derecha
 *   "listado": aparece suave y se retira un poco a la izquierda
 * Cambiar la `key` del componente vuelve a reproducir la entrada.
 */
export function VistaAnimada({
  tipo,
  saliendo,
  children,
}: {
  tipo: "detalle" | "listado";
  saliendo: boolean;
  children: ReactNode;
}) {
  return (
    <div className={`vista vista-${tipo}${saliendo ? " vista-saliendo" : ""}`}>
      {children}
    </div>
  );
}
