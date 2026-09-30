import { useEffect, useState } from "react";
import {
  listarOrdenes,
  actualizarOrden,
  abrirArchivoOC,
  type OrdenCompra,
} from "../api/ordenes-compra";
import { ApiError } from "../api/client";
import { ArchivoLink } from "../Components/ArchivoLink";
import { VistaAnimada } from "../Components/Animaciones";
import { useSalida } from "../Components/movimiento";
import "./Facturas.css";

/**
 * Qué tan seguro estuvo el detector con el número (30/09/2026).
 * Solo se destaca lo que conviene revisar; lo confiable no hace ruido.
 */
function ConfianzaBadge({ confianza, numero }: { confianza: string | null; numero: string | null }) {
  if (!numero) {
    return (
      <span className="factura-badge" style={{ background: "#fbe7e7", color: "#a33b3b", marginLeft: 8 }}>
        Capturar número
      </span>
    );
  }
  if (confianza === "baja" || confianza === "ninguna") {
    return (
      <span
        className="factura-badge"
        style={{ background: "#fdf1de", color: "#8a6d1f", marginLeft: 8 }}
        title="El número salió solo del nombre del archivo. Confírmalo contra el PDF."
      >
        Revisar
      </span>
    );
  }
  return null;
}

export function OrdenesCompra() {
  const [ordenes, setOrdenes] = useState<OrdenCompra[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [seleccionada, setSeleccionada] = useState<OrdenCompra | null>(null);
  const { saliendo, salir } = useSalida();

  useEffect(() => {
    listarOrdenes()
      .then(setOrdenes)
      .catch((err) => {
        setError(
          err instanceof ApiError
            ? err.message
            : "No se pudieron cargar las órdenes."
        );
      })
      .finally(() => setIsLoading(false));
  }, []);

  if (seleccionada) {
    return (
      <VistaAnimada key={`detalle-${seleccionada.id}`} tipo="detalle" saliendo={saliendo}>
      <DetalleOC
        oc={seleccionada}
        onVolver={() => salir(() => setSeleccionada(null))}
        onGuardado={(actualizada) => {
          setOrdenes((prev) =>
            prev.map((o) => (o.id === actualizada.id ? actualizada : o))
          );
          setSeleccionada(actualizada);
        }}
      />
      </VistaAnimada>
    );
  }

  const sinFactura = ordenes.filter((o) => o.sin_factura_30_dias).length;

  return (
    <VistaAnimada key="listado" tipo="listado" saliendo={saliendo}>
    <div className="facturas-panel">
      <div className="facturas-header">
        <div>
          <p className="facturas-eyebrow">Compras</p>
          <h2 className="facturas-title">Órdenes de compra</h2>
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
          {sinFactura > 0 && (
            <span
              className="factura-badge"
              style={{ background: "#fbe7e7", color: "#a33b3b", fontSize: 13 }}
            >
              {sinFactura} sin factura +30 días
            </span>
          )}
          <span className="facturas-count">
            {ordenes.length} {ordenes.length === 1 ? "orden" : "órdenes"}
          </span>
        </div>
      </div>

      {isLoading && <p className="facturas-status">Cargando órdenes…</p>}
      {error && (
        <p className="facturas-status facturas-status-error">{error}</p>
      )}

      {!isLoading && !error && (
        <table className="facturas-table">
          <thead>
            <tr>
              <th>Número OC</th>
              <th>Archivo</th>
              <th>Fecha recepción</th>
              <th>Facturas</th>
              <th>Alerta</th>
            </tr>
          </thead>
          <tbody>
            {ordenes.map((o) => (
              <tr
                key={o.id}
                className="facturas-row"
                onClick={() => salir(() => setSeleccionada(o))}
              >
                <td className="facturas-cell-strong">
                  {o.numero_oc ?? <span style={{ color: "#8a92a5" }}>Sin número</span>}
                  <ConfianzaBadge confianza={o.confianza_oc} numero={o.numero_oc} />
                </td>
                <td className="facturas-cell-muted">
                  {o.nombre_archivo ?? "—"}
                </td>
                <td className="facturas-cell-muted">
                  {o.fecha_recepcion.slice(0, 10)}
                </td>
                <td className="facturas-cell-mono">{o.facturas_asociadas}</td>
                <td>
                  {o.sin_factura_30_dias ? (
                    <span
                      className="factura-badge"
                      style={{ background: "#fbe7e7", color: "#a33b3b" }}
                    >
                      +30 días sin factura
                    </span>
                  ) : (
                    <span
                      className="factura-badge"
                      style={{ background: "#e5f0e8", color: "#2e7d5b" }}
                    >
                      OK
                    </span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {!isLoading && !error && ordenes.length === 0 && (
        <p className="facturas-status">
          No hay órdenes de compra registradas.
        </p>
      )}
    </div>
    </VistaAnimada>
  );
}

function DetalleOC({
  oc,
  onVolver,
  onGuardado,
}: {
  oc: OrdenCompra;
  onVolver: () => void;
  onGuardado: (actualizada: OrdenCompra) => void;
}) {
  const [numeroOc, setNumeroOc] = useState(oc.numero_oc ?? "");
  const [isSaving, setIsSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [guardadoOk, setGuardadoOk] = useState(false);

  async function handleGuardar() {
    setIsSaving(true);
    setError(null);
    setGuardadoOk(false);
    try {
      const actualizada = await actualizarOrden(oc.id, {
        numero_oc: numeroOc,
      });
      onGuardado(actualizada);
      setGuardadoOk(true);
    } catch (err) {
      setError(
        err instanceof ApiError
          ? err.message
          : "No se pudo guardar la orden."
      );
    } finally {
      setIsSaving(false);
    }
  }

  return (
    <div className="facturas-panel">
      <div className="factura-detalle-header">
        <span className="factura-volver" onClick={onVolver}>
          ← Volver
        </span>
        <span className="factura-detalle-divider">|</span>
        <h2 className="factura-detalle-title">OC {oc.numero_oc ?? "sin número"}</h2>
        <ConfianzaBadge confianza={oc.confianza_oc} numero={oc.numero_oc} />
        {oc.sin_factura_30_dias && (
          <span
            className="factura-badge"
            style={{
              background: "#fbe7e7",
              color: "#a33b3b",
              marginLeft: "auto",
            }}
          >
            +30 días sin factura
          </span>
        )}
      </div>

      <div className="factura-detalle-grid">
        <div className="factura-campo">
          <label className="factura-detalle-label">Número OC detectado</label>
          <div className="factura-campo-valor factura-campo-readonly">
            {oc.numero_oc_detectado ?? "—"}
          </div>
        </div>
        <div className="factura-campo">
          <label className="factura-detalle-label">Fecha de recepción</label>
          <div className="factura-campo-valor factura-campo-readonly">
            {oc.fecha_recepcion.slice(0, 10)}
          </div>
        </div>
        <div className="factura-campo">
          <label className="factura-detalle-label">Facturas asociadas</label>
          <div className="factura-campo-valor factura-campo-readonly factura-campo-mono">
            {oc.facturas_asociadas}
          </div>
        </div>
        <div className="factura-campo">
          <label className="factura-detalle-label">
            Número OC (editable)
          </label>
          <input
            className="factura-campo-input"
            value={numeroOc}
            onChange={(e) => setNumeroOc(e.target.value)}
            placeholder="OC-0000"
          />
        </div>
      </div>

      {oc.tiene_archivo && (
        <div className="factura-detalle-archivo">
          <label className="factura-detalle-label">Archivo adjunto</label>
          <ArchivoLink
            className="factura-file-card"
            accion={() => abrirArchivoOC(oc.id)}
          >
            <span className="factura-file-icon">OC</span>
            <div>
              <p className="factura-file-name">
                {oc.nombre_archivo ?? `orden_${oc.id}`}
              </p>
              <p className="factura-file-action">Abrir archivo</p>
            </div>
          </ArchivoLink>
        </div>
      )}

      {guardadoOk && (
        <p
          className="facturas-status"
          style={{ color: "#2e7d5b", padding: 0, margin: "0 24px" }}
        >
          Cambios guardados ✓
        </p>
      )}
      {error && (
        <p
          className="facturas-status facturas-status-error"
          style={{ padding: 0, margin: "0 24px" }}
        >
          {error}
        </p>
      )}

      <div className="factura-detalle-actions">
        <button className="factura-btn-secondary" onClick={onVolver}>
          Cancelar
        </button>
        <button
          className="factura-btn-primary"
          onClick={handleGuardar}
          disabled={isSaving}
        >
          {isSaving ? "Guardando…" : "Guardar cambios"}
        </button>
      </div>
    </div>
  );
}