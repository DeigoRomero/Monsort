import { useEffect, useState } from "react";
import {
  listarFacturas,
  obtenerFactura,
  actualizarFactura,
  urlPdfFactura,
  type FacturaListado,
  type FacturaDetalle as FacturaDetalleType,
} from "../api/facturas";
import { ApiError } from "../api/client";
import "./Facturas.css";

// Colores por palabra clave del nombre de estado — ajustar cuando se
// confirmen los nombres exactos que usa el equipo (ej. "Pendiente",
// "Verificada", "Rechazada", "Con error").
function estiloEstado(nombre: string) {
  const n = nombre.toLowerCase();
  if (n.includes("verific") || n.includes("aprob")) {
    return { bg: "#e5f0e8", color: "#2e7d5b" };
  }
  if (n.includes("rechaz") || n.includes("error")) {
    return { bg: "#fbe7e7", color: "#a33b3b" };
  }
  return { bg: "#fdf1de", color: "#8a6d1f" };
}

function EstadoBadge({ nombre }: { nombre: string }) {
  const style = estiloEstado(nombre);
  return (
    <span className="factura-badge" style={{ background: style.bg, color: style.color }}>
      {nombre}
    </span>
  );
}

function formatMonto(valor?: string | null) {
  if (!valor) return "—";
  const num = Number(valor);
  return Number.isNaN(num) ? valor : num.toLocaleString("es-MX", { minimumFractionDigits: 2 });
}

export function Facturas() {
  const [facturas, setFacturas] = useState<FacturaListado[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [idSeleccionado, setIdSeleccionado] = useState<number | null>(null);

  useEffect(() => {
    listarFacturas()
      .then(setFacturas)
      .catch((err) => {
        setError(err instanceof ApiError ? err.message : "No se pudieron cargar las facturas.");
      })
      .finally(() => setIsLoading(false));
  }, []);

  if (idSeleccionado !== null) {
    return (
      <FacturaDetalleView
        idFactura={idSeleccionado}
        onVolver={() => setIdSeleccionado(null)}
        onGuardado={(actualizada) => {
          setFacturas((prev) =>
            prev.map((f) =>
              f.id_factura === actualizada.id_factura
                ? {
                    ...f,
                    numero_oc: actualizada.numero_oc,
                    folio_interno: actualizada.folio_interno,
                    estado: actualizada.estado,
                  }
                : f
            )
          );
        }}
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
                onClick={() => setIdSeleccionado(f.id_factura)}
              >
                <td className="facturas-cell-strong">{f.folio_fiscal}</td>
                <td>{f.cliente}</td>
                <td className="facturas-cell-muted">{f.fecha}</td>
                <td className="facturas-cell-mono">${formatMonto(f.total)}</td>
                <td>
                  <EstadoBadge nombre={f.estado} />
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

function FacturaDetalleView({
  idFactura,
  onVolver,
  onGuardado,
}: {
  idFactura: number;
  onVolver: () => void;
  onGuardado: (f: FacturaDetalleType) => void;
}) {
  const [factura, setFactura] = useState<FacturaDetalleType | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [isSaving, setIsSaving] = useState(false);

  const [numeroOc, setNumeroOc] = useState("");
  const [folioInterno, setFolioInterno] = useState("");

  useEffect(() => {
    obtenerFactura(idFactura)
      .then((f) => {
        setFactura(f);
        setNumeroOc(f.numero_oc ?? "");
        setFolioInterno(f.folio_interno ?? "");
      })
      .catch((err) => {
        setError(err instanceof ApiError ? err.message : "No se pudo cargar la factura.");
      })
      .finally(() => setIsLoading(false));
  }, [idFactura]);

  async function handleGuardar() {
    setIsSaving(true);
    setError(null);
    try {
      const actualizada = await actualizarFactura(idFactura, {
        numero_oc: numeroOc || null,
        folio_interno: folioInterno || null,
      });
      setFactura(actualizada);
      onGuardado(actualizada);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "No se pudo guardar la factura.");
    } finally {
      setIsSaving(false);
    }
  }

  if (isLoading) {
    return (
      <div className="facturas-panel">
        <p className="facturas-status">Cargando factura…</p>
      </div>
    );
  }

  if (error || !factura) {
    return (
      <div className="facturas-panel">
        <p className="facturas-status facturas-status-error">
          {error ?? "No se encontró la factura."}
        </p>
      </div>
    );
  }

  return (
    <div className="facturas-panel">
      <div className="factura-detalle-header">
        <span className="factura-volver" onClick={onVolver}>
          ← Volver
        </span>
        <span className="factura-detalle-divider">|</span>
        <h2 className="factura-detalle-title">Factura {factura.folio_fiscal}</h2>
        <span className="factura-detalle-badge-wrap">
          <EstadoBadge nombre={factura.estado} />
        </span>
      </div>

      <div className="factura-detalle-grid">
        <Campo label="Cliente" value={factura.cliente} readOnly />
        <Campo label="RFC" value={factura.rfc} readOnly />
        <Campo label="Fecha" value={factura.fecha} readOnly />
        <Campo
          label="Fecha de liquidación"
          value={factura.fecha_liquidacion ?? ""}
          readOnly
        />
        <Campo label="Subtotal" value={formatMonto(factura.subtotal)} readOnly mono />
        <Campo label="IVA" value={formatMonto(factura.iva)} readOnly mono />
        <Campo label="Total" value={formatMonto(factura.total)} readOnly mono full />

        <Campo label="Número de OC" value={numeroOc} onChange={setNumeroOc} />
        <Campo label="Folio interno" value={folioInterno} onChange={setFolioInterno} />
      </div>

      {factura.conceptos.length > 0 && (
        <div className="factura-conceptos">
          <label className="factura-detalle-label">Conceptos</label>
          <table className="factura-conceptos-table">
            <thead>
              <tr>
                <th>Descripción</th>
                <th>Cant.</th>
                <th>P. Unitario</th>
                <th>Importe</th>
              </tr>
            </thead>
            <tbody>
              {factura.conceptos.map((c, i) => (
                <tr key={i}>
                  <td>{c.descripcion ?? "—"}</td>
                  <td>{c.cantidad ?? "—"}</td>
                  <td>{c.precio_unitario ?? "—"}</td>
                  <td>{c.importe ?? "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <div className="factura-detalle-archivo">
        <label className="factura-detalle-label">Archivo</label>
        {factura.tiene_pdf ? (
          <a
            className="factura-file-card"
            href={urlPdfFactura(factura.id_factura)}
            target="_blank"
            rel="noreferrer"
          >
            <span className="factura-file-icon">PDF</span>
            <div>
              <p className="factura-file-name">
                factura_{factura.folio_interno ?? factura.id_factura}.pdf
              </p>
              <p className="factura-file-action">Ver documento</p>
            </div>
          </a>
        ) : (
          <p className="facturas-status" style={{ padding: 0, textAlign: "left" }}>
            Esta factura no tiene PDF adjunto.
          </p>
        )}
      </div>

      {error && <p className="facturas-status facturas-status-error">{error}</p>}

      <div className="factura-detalle-actions">
        <button className="factura-btn-secondary" onClick={onVolver}>
          Cancelar
        </button>
        <button className="factura-btn-primary" onClick={handleGuardar} disabled={isSaving}>
          {isSaving ? "Guardando…" : "Guardar cambios"}
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
        <div className="factura-campo-valor factura-campo-readonly">{value || "—"}</div>
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