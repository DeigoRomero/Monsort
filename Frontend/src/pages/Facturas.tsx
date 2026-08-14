import { useEffect, useState } from "react";
import { listarFacturas, type Factura } from "../api/facturas";
import { ApiError } from "../api/client";
import "./Facturas.css";

// Colores genéricos por id_estado — ajustar cuando se confirmen los
// estados reales definidos en la base de datos (tabla Estados).
const ESTADO_STYLES: Record<number, { label: string; bg: string; color: string }> = {
  1: { label: "Pendiente", bg: "#fdf1de", color: "#8a6d1f" },
  2: { label: "Verificada", bg: "#e5f0e8", color: "#2e7d5b" },
  3: { label: "Rechazada", bg: "#fbe7e7", color: "#a33b3b" },
};

function EstadoBadge({ idEstado, nombre }: { idEstado: number; nombre?: string }) {
  const style = ESTADO_STYLES[idEstado] ?? {
    label: nombre ?? `Estado ${idEstado}`,
    bg: "#eef0f3",
    color: "#566078",
  };
  return (
    <span
      className="factura-badge"
      style={{ background: style.bg, color: style.color }}
    >
      {nombre ?? style.label}
    </span>
  );
}

export function Facturas() {
  const [facturas, setFacturas] = useState<Factura[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [seleccionada, setSeleccionada] = useState<Factura | null>(null);

  useEffect(() => {
    listarFacturas()
      .then(setFacturas)
      .catch((err) => {
        if (err instanceof ApiError) {
          setError(err.message);
        } else {
          setError("No se pudieron cargar las facturas.");
        }
      })
      .finally(() => setIsLoading(false));
  }, []);

  if (seleccionada) {
    return (
      <FacturaDetalle
        factura={seleccionada}
        onVolver={() => setSeleccionada(null)}
      />
    );
  }

  return (
    <div className="facturas-panel">
      <div className="facturas-header">
        <div>
          <p className="facturas-eyebrow">Facturas recibidas</p>
          <h2 className="facturas-title">Bandeja de verificación</h2>
        </div>
        <span className="facturas-count">
          {facturas.length} {facturas.length === 1 ? "factura" : "facturas"}
        </span>
      </div>

      {isLoading && <p className="facturas-status">Cargando facturas…</p>}
      {error && <p className="facturas-status facturas-status-error">{error}</p>}

      {!isLoading && !error && (
        <table className="facturas-table">
          <thead>
            <tr>
              <th>Folio</th>
              <th>Cliente</th>
              <th>Fecha</th>
              <th>Total</th>
              <th>Estado</th>
            </tr>
          </thead>
          <tbody>
            {facturas.map((f) => (
              <tr
                key={f.id_factura}
                className="facturas-row"
                onClick={() => setSeleccionada(f)}
              >
                <td className="facturas-cell-strong">{f.folio}</td>
                <td>{f.cliente}</td>
                <td className="facturas-cell-muted">{f.fecha}</td>
                <td className="facturas-cell-mono">${f.monto}</td>
                <td>
                  <EstadoBadge idEstado={f.id_estado} nombre={f.nombre_estado} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {!isLoading && !error && facturas.length === 0 && (
        <p className="facturas-status">No hay facturas por mostrar.</p>
      )}
    </div>
  );
}

function FacturaDetalle({
  factura,
  onVolver,
}: {
  factura: Factura;
  onVolver: () => void;
}) {
  const [folioInterno, setFolioInterno] = useState(factura.folio_interno ?? "");
  const [ordenCompra, setOrdenCompra] = useState(factura.orden_compra ?? "");
  const [subtotal, setSubtotal] = useState(factura.subtotal ?? "");
  const [iva, setIva] = useState(factura.iva ?? "");
  const [total, setTotal] = useState(factura.total ?? factura.monto ?? "");

  function handleGuardar() {
    // TODO: conectar con el endpoint PUT/PATCH de facturas cuando exista
    console.log("Guardar factura", {
      id_factura: factura.id_factura,
      folioInterno,
      ordenCompra,
      subtotal,
      iva,
      total,
    });
  }

  return (
    <div className="facturas-panel">
      <div className="factura-detalle-header">
        <span className="factura-volver" onClick={onVolver}>
          ← Volver
        </span>
        <span className="factura-detalle-divider">|</span>
        <h2 className="factura-detalle-title">Factura {factura.folio}</h2>
        <span className="factura-detalle-badge-wrap">
          <EstadoBadge idEstado={factura.id_estado} nombre={factura.nombre_estado} />
        </span>
      </div>

      <div className="factura-detalle-grid">
        <Campo label="RFC" value={factura.rfc} readOnly />
        <Campo
          label="Folio interno"
          value={folioInterno}
          onChange={setFolioInterno}
        />
        <Campo
          label="Orden de compra"
          value={ordenCompra}
          onChange={setOrdenCompra}
        />
        <Campo label="Fecha" value={factura.fecha} readOnly />
        <Campo label="Subtotal" value={subtotal} onChange={setSubtotal} mono />
        <Campo label="IVA" value={iva} onChange={setIva} mono />
        <Campo label="Total" value={total} onChange={setTotal} mono full />
      </div>

      <div className="factura-detalle-archivo">
        <label className="factura-detalle-label">Archivo adjunto</label>
        <div className="factura-file-card">
          <span className="factura-file-icon">PDF</span>
          <div>
            <p className="factura-file-name">factura_{factura.folio}.pdf</p>
            <p className="factura-file-action">Ver documento</p>
          </div>
        </div>
      </div>

      <div className="factura-detalle-actions">
        <button className="factura-btn-secondary" onClick={onVolver}>
          Cancelar
        </button>
        <button className="factura-btn-primary" onClick={handleGuardar}>
          Guardar cambios
        </button>
      </div>
    </div>
  );
}

function Campo({
  label,
  value,
  onChange,
  readOnly,
  mono,
  full,
}: {
  label: string;
  value: string;
  onChange?: (v: string) => void;
  readOnly?: boolean;
  mono?: boolean;
  full?: boolean;
}) {
  return (
    <div className={full ? "factura-campo factura-campo-full" : "factura-campo"}>
      <label className="factura-detalle-label">{label}</label>
      {readOnly ? (
        <div className="factura-campo-valor factura-campo-readonly">
          {value || "—"}
        </div>
      ) : (
        <input
          className={`factura-campo-input${mono ? " factura-campo-mono" : ""}`}
          value={value}
          onChange={(e) => onChange?.(e.target.value)}
          placeholder="—"
        />
      )}
    </div>
  );
}