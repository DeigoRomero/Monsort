import { useEffect, useState } from "react";
import {
  listarClientes,
  obtenerCliente,
  crearCliente,
  actualizarCliente,
  type Cliente,
  type ClienteDetalle,
} from "../api/clientes";
import { ApiError } from "../api/client";
import "./Facturas.css";

export function Clientes() {
  const [clientes, setClientes] = useState<Cliente[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [soloActivos, setSoloActivos] = useState(true);
  const [seleccionado, setSeleccionado] = useState<number | null>(null);
  const [mostrarCrear, setMostrarCrear] = useState(false);

  useEffect(() => {
    cargar();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [soloActivos]);

  function cargar() {
    setIsLoading(true);
    listarClientes(soloActivos)
      .then(setClientes)
      .catch((err) => {
        setError(
          err instanceof ApiError
            ? err.message
            : "No se pudieron cargar los clientes."
        );
      })
      .finally(() => setIsLoading(false));
  }

  if (seleccionado !== null) {
    return (
      <DetalleCliente
        idCliente={seleccionado}
        onVolver={() => {
          setSeleccionado(null);
          cargar();
        }}
      />
    );
  }

  if (mostrarCrear) {
    return (
      <CrearCliente
        onVolver={() => {
          setMostrarCrear(false);
          cargar();
        }}
      />
    );
  }

  return (
    <div className="facturas-panel">
      <div className="facturas-header">
        <div>
          <p className="facturas-eyebrow">Gestión</p>
          <h2 className="facturas-title">Clientes</h2>
        </div>
        <div style={{ display: "flex", gap: 10, alignItems: "center" }}>
          <label className="facturas-checkbox">
            <input
              type="checkbox"
              checked={soloActivos}
              onChange={(e) => setSoloActivos(e.target.checked)}
            />
            Solo activos
          </label>
          <button
            className="factura-btn-primary"
            onClick={() => setMostrarCrear(true)}
          >
            + Nuevo cliente
          </button>
        </div>
      </div>

      {isLoading && <p className="facturas-status">Cargando clientes…</p>}
      {error && (
        <p className="facturas-status facturas-status-error">{error}</p>
      )}

      {!isLoading && !error && (
        <table className="facturas-table">
          <thead>
            <tr>
              <th>RFC</th>
              <th>Nombre</th>
              <th>Días plazo pago</th>
              <th>Estado</th>
              <th>Alta</th>
            </tr>
          </thead>
          <tbody>
            {clientes.map((c) => (
              <tr
                key={c.id}
                className={`facturas-row${!c.activo ? " facturas-row-cancelada" : ""}`}
                onClick={() => setSeleccionado(c.id)}
              >
                <td className="facturas-cell-strong facturas-cell-mono">
                  {c.rfc}
                </td>
                <td>{c.nombre}</td>
                <td className="facturas-cell-mono">
                  {c.dias_plazo_pago !== null
                    ? `${c.dias_plazo_pago} días`
                    : "—"}
                </td>
                <td>
                  <span
                    className="factura-badge"
                    style={
                      c.activo
                        ? { background: "#e5f0e8", color: "#2e7d5b" }
                        : { background: "#eef0f3", color: "#8a92a5" }
                    }
                  >
                    {c.activo ? "Activo" : "Inactivo"}
                  </span>
                </td>
                <td className="facturas-cell-muted">
                  {c.fecha_creacion.slice(0, 10)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {!isLoading && !error && clientes.length === 0 && (
        <p className="facturas-status">No hay clientes registrados.</p>
      )}
    </div>
  );
}

function DetalleCliente({
  idCliente,
  onVolver,
}: {
  idCliente: number;
  onVolver: () => void;
}) {
  const [cliente, setCliente] = useState<ClienteDetalle | null>(null);
  const [diasPlazo, setDiasPlazo] = useState("");
  const [isLoading, setIsLoading] = useState(true);
  const [isSaving, setIsSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [guardadoOk, setGuardadoOk] = useState(false);

  useEffect(() => {
    obtenerCliente(idCliente)
      .then((c) => {
        setCliente(c);
        setDiasPlazo(
          c.dias_plazo_pago !== null ? String(c.dias_plazo_pago) : ""
        );
      })
      .catch((err) => {
        setError(
          err instanceof ApiError
            ? err.message
            : "No se pudo cargar el cliente."
        );
      })
      .finally(() => setIsLoading(false));
  }, [idCliente]);

  async function handleGuardar() {
    setIsSaving(true);
    setError(null);
    setGuardadoOk(false);
    try {
      await actualizarCliente(idCliente, {
        dias_plazo_pago: diasPlazo ? Number(diasPlazo) : null,
      });
      setGuardadoOk(true);
    } catch (err) {
      setError(
        err instanceof ApiError ? err.message : "No se pudo guardar."
      );
    } finally {
      setIsSaving(false);
    }
  }

  async function handleDesactivar() {
    if (!confirm("¿Desactivar este cliente? No se eliminará.")) return;
    try {
      await actualizarCliente(idCliente, { activo: false });
      onVolver();
    } catch (err) {
      setError(
        err instanceof ApiError ? err.message : "No se pudo desactivar."
      );
    }
  }

  if (isLoading)
    return (
      <div className="facturas-panel">
        <p className="facturas-status">Cargando…</p>
      </div>
    );

  if (!cliente)
    return (
      <div className="facturas-panel">
        <p className="facturas-status facturas-status-error">{error}</p>
      </div>
    );

  return (
    <div className="facturas-panel">
      <div className="factura-detalle-header">
        <span className="factura-volver" onClick={onVolver}>
          ← Volver
        </span>
        <span className="factura-detalle-divider">|</span>
        <h2 className="factura-detalle-title">{cliente.nombre}</h2>
        <span
          className="factura-badge"
          style={{
            marginLeft: "auto",
            ...(cliente.activo
              ? { background: "#e5f0e8", color: "#2e7d5b" }
              : { background: "#eef0f3", color: "#8a92a5" }),
          }}
        >
          {cliente.activo ? "Activo" : "Inactivo"}
        </span>
      </div>

      <div className="factura-detalle-grid">
        <div className="factura-campo">
          <label className="factura-detalle-label">RFC</label>
          <div className="factura-campo-valor factura-campo-readonly factura-campo-mono">
            {cliente.rfc}
          </div>
        </div>
        <div className="factura-campo">
          <label className="factura-detalle-label">Alta</label>
          <div className="factura-campo-valor factura-campo-readonly">
            {cliente.fecha_creacion.slice(0, 10)}
          </div>
        </div>
        <div className="factura-campo">
          <label className="factura-detalle-label">Total facturas</label>
          <div className="factura-campo-valor factura-campo-readonly factura-campo-mono">
            {cliente.total_facturas}
          </div>
        </div>
        <div className="factura-campo">
          <label className="factura-detalle-label">
            Total órdenes de compra
          </label>
          <div className="factura-campo-valor factura-campo-readonly factura-campo-mono">
            {cliente.total_ordenes}
          </div>
        </div>
        <div className="factura-campo">
          <label className="factura-detalle-label">
            Días de plazo de pago
          </label>
          <input
            className="factura-campo-input"
            type="number"
            min={0}
            value={diasPlazo}
            onChange={(e) => setDiasPlazo(e.target.value)}
            placeholder="Ej. 30"
          />
        </div>
      </div>

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
        {cliente.activo && (
          <button className="cancelar-btn" onClick={handleDesactivar}>
            Desactivar cliente
          </button>
        )}
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

function CrearCliente({ onVolver }: { onVolver: () => void }) {
  const [rfc, setRfc] = useState("");
  const [nombre, setNombre] = useState("");
  const [diasPlazo, setDiasPlazo] = useState("");
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleCrear() {
    setIsLoading(true);
    setError(null);
    try {
      await crearCliente({
        rfc,
        nombre,
        dias_plazo_pago: diasPlazo ? Number(diasPlazo) : null,
      });
      onVolver();
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) {
        setError("Ya existe un cliente con ese RFC.");
      } else {
        setError(
          err instanceof ApiError
            ? err.message
            : "No se pudo crear el cliente."
        );
      }
    } finally {
      setIsLoading(false);
    }
  }

  return (
    <div className="facturas-panel">
      <div className="factura-detalle-header">
        <span className="factura-volver" onClick={onVolver}>
          ← Volver
        </span>
        <span className="factura-detalle-divider">|</span>
        <h2 className="factura-detalle-title">Nuevo cliente</h2>
      </div>

      <div className="factura-detalle-grid">
        <div className="factura-campo">
          <label className="factura-detalle-label">RFC</label>
          <input
            className="factura-campo-input factura-campo-mono"
            value={rfc}
            onChange={(e) => setRfc(e.target.value.toUpperCase())}
            placeholder="PIN040713FL9"
          />
        </div>
        <div className="factura-campo">
          <label className="factura-detalle-label">Nombre</label>
          <input
            className="factura-campo-input"
            value={nombre}
            onChange={(e) => setNombre(e.target.value)}
            placeholder="PARKER INDUSTRIAL"
          />
        </div>
        <div className="factura-campo">
          <label className="factura-detalle-label">
            Días de plazo de pago
          </label>
          <input
            className="factura-campo-input"
            type="number"
            min={0}
            value={diasPlazo}
            onChange={(e) => setDiasPlazo(e.target.value)}
            placeholder="30"
          />
        </div>
      </div>

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
          onClick={handleCrear}
          disabled={isLoading || !rfc || !nombre}
        >
          {isLoading ? "Creando…" : "Crear cliente"}
        </button>
      </div>
    </div>
  );
}