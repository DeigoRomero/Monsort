import { useCallback, useEffect, useRef, useState } from "react";
import { listarNotificaciones, type Notificacion } from "../api/notificaciones";
import "./Notificaciones.css";

// El backend revisa Gmail cada 5 minutos y deja una notificación cuando algo
// cambió. Aquí se consulta cada minuto: el aviso aparece a más tardar un
// minuto después de cada revisión.
const INTERVALO_MS = 60_000;
const CLAVE_VISTO = "monsort.notificaciones.ultimo_visto";
const DURACION_AVISO_MS = 12_000;

function leerVisto(): number | null {
  try {
    const v = localStorage.getItem(CLAVE_VISTO);
    return v === null ? null : Number(v) || 0;
  } catch {
    return null;
  }
}

function guardarVisto(id: number) {
  try {
    localStorage.setItem(CLAVE_VISTO, String(id));
  } catch {
    /* sin storage: solo se pierde el "leído" entre recargas */
  }
}

function haceCuanto(iso: string | null): string {
  if (!iso) return "sin revisar todavía";
  const seg = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
  if (seg < 60) return "hace un momento";
  const min = Math.round(seg / 60);
  if (min < 60) return `hace ${min} min`;
  const h = Math.round(min / 60);
  return h < 24 ? `hace ${h} h` : new Date(iso).toLocaleString("es-MX");
}

export function Notificaciones({ onIr }: { onIr: (seccion: string) => void }) {
  const [lista, setLista] = useState<Notificacion[]>([]);
  const [avisos, setAvisos] = useState<Notificacion[]>([]);
  const [ultimaRevision, setUltimaRevision] = useState<string | null>(null);
  const [abierto, setAbierto] = useState(false);
  const [visto, setVisto] = useState<number>(() => leerVisto() ?? 0);
  const [, forzar] = useState(0);
  const ultimoConocido = useRef<number | null>(null);

  const consultar = useCallback(async () => {
    try {
      const r = await listarNotificaciones(0, 30);
      setUltimaRevision(r.ultima_revision_gmail);
      setLista(r.notificaciones);

      if (ultimoConocido.current === null) {
        // Primera carga: no se muestran como emergentes los avisos viejos.
        ultimoConocido.current = r.ultimo_id;
        if (leerVisto() === null) {
          guardarVisto(r.ultimo_id);
          setVisto(r.ultimo_id);
        }
        return;
      }
      const nuevas = r.notificaciones.filter((n) => n.id > (ultimoConocido.current ?? 0));
      ultimoConocido.current = Math.max(ultimoConocido.current, r.ultimo_id);
      if (nuevas.length) {
        setAvisos((prev) => [...nuevas.reverse(), ...prev].slice(0, 4));
        // Aviso del sistema operativo si la pestaña no está a la vista
        if (document.hidden && "Notification" in window && Notification.permission === "granted") {
          for (const n of nuevas) {
            new Notification("Monsort", { body: n.titulo, tag: `monsort-${n.id}` });
          }
        }
      }
    } catch {
      // Sin red o sesión vencida: el siguiente ciclo lo reintenta
    }
  }, []);

  useEffect(() => {
    consultar();
    const id = window.setInterval(consultar, INTERVALO_MS);
    // Refresca también el "hace X min" sin pedir nada al servidor
    const reloj = window.setInterval(() => forzar((x) => x + 1), 30_000);
    const alVolver = () => !document.hidden && consultar();
    document.addEventListener("visibilitychange", alVolver);
    return () => {
      window.clearInterval(id);
      window.clearInterval(reloj);
      document.removeEventListener("visibilitychange", alVolver);
    };
  }, [consultar]);

  useEffect(() => {
    if (!avisos.length) return;
    const id = window.setTimeout(() => setAvisos((prev) => prev.slice(0, -1)), DURACION_AVISO_MS);
    return () => window.clearTimeout(id);
  }, [avisos]);

  const sinLeer = lista.filter((n) => n.id > visto).length;

  function abrirPanel() {
    const abrir = !abierto;
    setAbierto(abrir);
    if (abrir && lista.length) {
      const maximo = Math.max(...lista.map((n) => n.id));
      guardarVisto(maximo);
      // Se marca leído al CERRAR, para que al abrir se vea qué era nuevo
      window.setTimeout(() => setVisto(maximo), 4000);
    }
  }

  function ir(n: Notificacion) {
    if (n.seccion) onIr(n.seccion);
    setAvisos((prev) => prev.filter((a) => a.id !== n.id));
    setAbierto(false);
  }

  const puedePedirPermiso =
    "Notification" in window && Notification.permission === "default";

  return (
    <>
      <button
        className="dashboard-nav-item notif-boton"
        onClick={abrirPanel}
        aria-expanded={abierto}
        aria-label={`Notificaciones${sinLeer ? `, ${sinLeer} sin leer` : ""}`}
      >
        <span>Notificaciones</span>
        {sinLeer > 0 && <span className="notif-contador">{sinLeer > 9 ? "9+" : sinLeer}</span>}
      </button>
      <p className="notif-revision" title={ultimaRevision ?? undefined}>
        Gmail revisado {haceCuanto(ultimaRevision)}
      </p>

      {abierto && (
        <div className="notif-panel" role="dialog" aria-label="Notificaciones">
          <div className="notif-panel-encabezado">
            <strong>Notificaciones</strong>
            <button className="notif-cerrar" onClick={() => setAbierto(false)} aria-label="Cerrar">
              ×
            </button>
          </div>
          {puedePedirPermiso && (
            <button className="notif-permiso" onClick={() => Notification.requestPermission()}>
              Avisarme aunque esté en otra pestaña
            </button>
          )}
          {lista.length === 0 && <p className="notif-vacio">Sin cambios recientes.</p>}
          <ul className="notif-lista">
            {lista.map((n) => (
              <li
                key={n.id}
                className={`notif-item notif-${n.nivel}${n.id > visto ? " notif-nueva" : ""}`}
                onClick={() => ir(n)}
              >
                <p className="notif-titulo">{n.titulo}</p>
                {n.detalle && <p className="notif-detalle">{n.detalle}</p>}
                <p className="notif-fecha">{new Date(n.fecha).toLocaleString("es-MX")}</p>
              </li>
            ))}
          </ul>
        </div>
      )}

      <div className="notif-avisos" aria-live="polite">
        {avisos.map((n) => (
          <div key={n.id} className={`notif-aviso notif-${n.nivel}`} onClick={() => ir(n)}>
            <p className="notif-titulo">{n.titulo}</p>
            {n.detalle && <p className="notif-detalle">{n.detalle}</p>}
            <button
              className="notif-cerrar"
              aria-label="Descartar"
              onClick={(e) => {
                e.stopPropagation();
                setAvisos((prev) => prev.filter((a) => a.id !== n.id));
              }}
            >
              ×
            </button>
          </div>
        ))}
      </div>
    </>
  );
}
