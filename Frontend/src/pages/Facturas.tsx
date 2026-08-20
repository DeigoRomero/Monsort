import { useEffect, useState } from "react";
import {
  listarFacturas,
  listarEstados,
  obtenerFactura,
  listarOcsCandidatas,
  vincularOc,
  actualizarFactura,
  cancelarFactura,
  urlPdfFactura,
  descargarReporteGeneral,
  descargarReporteDetalle,
  type FacturaListado,
  type FacturaDetalle as FacturaDetalleType,
  type OrdenCompraCandidata,
  type FiltrosFacturas,
  type ResumenFacturas,
  type EstadoOpcion,
} from "../api/facturas";
import { ApiError } from "../api/client";
import { CancelarModal } from "../components/CancelarModal";
import "./Facturas.css";

function estiloEstado(nombre: string) {
  const n = nombre.toLowerCase();
  if (n.includes("cancel")) return { bg: "#eef0f3", color: "#8a92a5" };
  if (n.includes("verific") || n.includes("aprob")) return { bg: "#e5f0e8", color: "#2e7d5b" };
  if (n.includes("rechaz") || n.includes("error")) return { bg: "#fbe7e7", color: "#a33b3b" };
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

function formatMonto(valor?: number | null) {
  if (valor === null || valor === undefined) return "—";
  return valor.toLocaleString("es-MX", { minimumFractionDigits: 2 });
}

export function Facturas() {
  const [facturas, setFacturas] = useState<FacturaListado[]>([]);
  const [resumen, setResumen] = useState<ResumenFacturas | null>(null);
  const [estados, setEstados] = useState<EstadoOpcion[]>([]);
  const [pagina, setPagina] = useState(1);
  const [totalPaginas, setTotalPaginas] = useState(1);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [idSeleccionado, setIdSeleccionado] = useState<number | null>(null);

  // filtros
  const [q, setQ] = useState("");
  const [cliente, setCliente] = useState("");
  const [numeroOc, setNumeroOc] = useState("");
  const [conCp, setConCp] = useState<"todos" | "true" | "false">("todos");
  const [incluirCanceladas, setIncluirCanceladas] = useState(false);
  const [fechaDesde, setFechaDesde] = useState("");
  const [fechaHasta, setFechaHasta] = useState("");
  const [estadoFiltro, setEstadoFiltro] = useState(""); // filtrado en frontend, ver nota

  const [descargandoReporte, setDescargandoReporte] = useState(false);

  useEffect(() => {
    listarEstados().then(setEstados).catch(() => {});
  }, []);

  // debounce de búsqueda y filtros
  useEffect(() => {
    const id = setTimeout(() => cargar(1), 400);
    return () => clearTimeout(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [q, cliente, numeroOc, estadoFiltro, conCp, incluirCanceladas, fechaDesde, fechaHasta]);

  function filtrosActuales(paginaOverride?: number): FiltrosFacturas {
    return {
      q: q || undefined,
      cliente: cliente || undefined,
      numero_oc: numeroOc || undefined,
      estado: estadoFiltro || undefined,
      con_cp: conCp === "todos" ? undefined : conCp === "true",
      incluir_canceladas: incluirCanceladas,
      fecha_desde: fechaDesde || undefined,
      fecha_hasta: fechaHasta || undefined,
      pagina: paginaOverride ?? pagina,
      por_pagina: 50,
    };
  }

  function cargar(paginaObjetivo: number) {
    setIsLoading(true);
    setError(null);
    listarFacturas(filtrosActuales(paginaObjetivo))
      .then((res) => {
        setFacturas(res.facturas);
        setResumen(res.resumen);
        setPagina(res.pagina);
        setTotalPaginas(res.total_paginas);
      })
      .catch((err) => {
        setError(err instanceof ApiError ? err.message : "No se pudieron cargar las facturas.");
      })
      .finally(() => setIsLoading(false));
  }

  async function handleReporteGeneral() {
    setDescargandoReporte(true);
    try {
      await descargarReporteGeneral(filtrosActuales());
    } catch {
      setError("No se pudo generar el reporte general.");
    } finally {
      setDescargandoReporte(false);
    }
  }

  if (idSeleccionado !== null) {
    return (
      <FacturaDetalleView
        idFactura={idSeleccionado}
        onVolver={() => {
          setIdSeleccionado(null);
          cargar(pagina);
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
        <button
          className="factura-btn-primary"
          onClick={handleReporteGeneral}
          disabled={descargandoReporte}
        >
          {descargandoReporte ? "Generando…" : "Reporte general"}
        </button>
      </div>

      <div className="facturas-filtros">
        <input
          className="field-input facturas-buscador"
          placeholder="Buscar por cliente, UUID, folio interno u OC…"
          value={q}
          onChange={(e) => setQ(e.target.value)}
        />

        <div className="facturas-filtros-grid">
          <input
            className="field-input"
            placeholder="Cliente"
            value={cliente}
            onChange={(e) => setCliente(e.target.value)}
          />
          <input
            className="field-input"
            placeholder="Número de OC"
            value={numeroOc}
            onChange={(e) => setNumeroOc(e.target.value)}
          />
          <select
            className="field-input"
            value={estadoFiltro}
            onChange={(e) => setEstadoFiltro(e.target.value)}
          >
            <option value="">Todos los estados</option>
            {estados.map((e) => (
              <option key={e.id_estado} value={e.nombre_estado}>
                {e.nombre_estado}
              </option>
            ))}
          </select>
          <select
            className="field-input"
            value={conCp}
            onChange={(e) => setConCp(e.target.value as typeof conCp)}
          >
            <option value="todos">Con y sin CP</option>
            <option value="true">Solo con CP</option>
            <option value="false">Solo sin CP</option>
          </select>
          <input
            className="field-input"
            type="date"
            value={fechaDesde}
            onChange={(e) => setFechaDesde(e.target.value)}
          />
          <input
            className="field-input"
            type="date"
            value={fechaHasta}
            onChange={(e) => setFechaHasta(e.target.value)}
          />
        </div>

        <label className="facturas-checkbox">
          <input
            type="checkbox"
            checked={incluirCanceladas}
            onChange={(e) => setIncluirCanceladas(e.target.checked)}
          />
          Incluir canceladas
        </label>
      </div>

      {resumen && (
        <div className="facturas-resumen">
          <div>
            <p className="facturas-resumen-label">Facturas</p>
            <p className="facturas-resumen-valor">{resumen.total_facturas}</p>
          </div>
          <div>
            <p className="facturas-resumen-label">Total MXN</p>
            <p className="facturas-resumen-valor">${formatMonto(resumen.total_mxn)}</p>
          </div>
          <div>
            <p className="facturas-resumen-label">Con CP</p>
            <p className="facturas-resumen-valor">{resumen.total_con_cp}</p>
          </div>
          <div>
            <p className="facturas-resumen-label">Sin CP</p>
            <p className="facturas-resumen-valor">{resumen.total_sin_cp}</p>
          </div>
          <div>
            <p className="facturas-resumen-label">Canceladas</p>
            <p className="facturas-resumen-valor">{resumen.total_canceladas}</p>
          </div>
        </div>
      )}

      {isLoading && <p className="facturas-status">Cargando facturas…</p>}
      {error && <p className="facturas-status facturas-status-error">{error}</p>}

      {!isLoading && !error && (
        <>
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
                  className={`facturas-row${f.estado.toLowerCase().includes("cancel") ? " facturas-row-cancelada" : ""}`}
                  onClick={() => setIdSeleccionado(f.id_factura)}
                >
                  <td className="facturas-cell-strong">{f.folio_fiscal}</td>
                  <td>{f.cliente}</td>
                  <td className="facturas-cell-muted">{f.fecha}</td>
                  <td className="facturas-cell-mono">
                    ${formatMonto(f.total)} {f.moneda && f.moneda !== "MXN" ? f.moneda : ""}
                  </td>
                  <td>
                    <EstadoBadge nombre={f.estado} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>

          {facturas.length === 0 && (
            <p className="facturas-status">No hay facturas que coincidan con los filtros.</p>
          )}

          <div className="facturas-paginacion">
            <button
              className="factura-btn-secondary"
              disabled={pagina <= 1}
              onClick={() => cargar(pagina - 1)}
            >
              ← Anterior
            </button>
            <span className="facturas-paginacion-info">
              Página {pagina} de {totalPaginas}
            </span>
            <button
              className="factura-btn-secondary"
              disabled={pagina >= totalPaginas}
              onClick={() => cargar(pagina + 1)}
            >
              Siguiente →
            </button>
          </div>
        </>
      )}
    </div>
  );
}

function FacturaDetalleView({
  idFactura,
  onVolver,
}: {
  idFactura: number;
  onVolver: () => void;
}) {
  const [factura, setFactura] = useState<FacturaDetalleType | null>(null);
  const [candidatas, setCandidatas] = useState<OrdenCompraCandidata[]>([]);
  const [mostrarCandidatas, setMostrarCandidatas] = useState(false);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [isSaving, setIsSaving] = useState(false);
  const [isVinculando, setIsVinculando] = useState(false);
  const [descargando, setDescargando] = useState(false);
  const [mostrarCancelar, setMostrarCancelar] = useState(false);
  const [guardadoOk, setGuardadoOk] = useState(false);

  const [numeroOc, setNumeroOc] = useState("");
  const [folioInterno, setFolioInterno] = useState("");
  const [fechaValidacion, setFechaValidacion] = useState("");

  useEffect(() => {
    cargarFactura();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [idFactura]);

  function cargarFactura() {
    setIsLoading(true);
    obtenerFactura(idFactura)
      .then((f) => {
        setFactura(f);
        setNumeroOc(f.numero_oc ?? "");
        setFolioInterno(f.folio_interno ?? "");
        setFechaValidacion(f.fecha_validacion ?? "");
      })
      .catch((err) => {
        setError(err instanceof ApiError ? err.message : "No se pudo cargar la factura.");
      })
      .finally(() => setIsLoading(false));
  }

  function handleVerCandidatas() {
    setMostrarCandidatas(true);
    listarOcsCandidatas(idFactura)
      .then(setCandidatas)
      .catch(() => setError("No se pudieron cargar las órdenes de compra candidatas."));
  }

  async function handleVincular(idOrdenCompra: number) {
    setIsVinculando(true);
    setError(null);
    try {
      const actualizada = await vincularOc(idFactura, idOrdenCompra);
      setFactura(actualizada);
      setNumeroOc(actualizada.numero_oc ?? "");
      setMostrarCandidatas(false);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "No se pudo vincular la orden de compra.");
    } finally {
      setIsVinculando(false);
    }
  }

  async function handleGuardar() {
    setIsSaving(true);
    setError(null);
    setGuardadoOk(false);
    try {
      await actualizarFactura(idFactura, {
        numero_oc: numeroOc || null,
        folio_interno: folioInterno || null,
        fecha_validacion: fechaValidacion || null,
      });
      setGuardadoOk(true);
      cargarFactura();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "No se pudo guardar la factura.");
    } finally {
      setIsSaving(false);
    }
  }

  async function handleCancelar(motivo: string) {
    try {
      await cancelarFactura(idFactura, motivo);
      setMostrarCancelar(false);
      onVolver();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "No se pudo cancelar la factura.");
    }
  }

  async function handleReporteDetalle() {
    setDescargando(true);
    try {
      await descargarReporteDetalle(idFactura);
    } catch {
      setError("No se pudo generar el reporte de esta factura.");
    } finally {
      setDescargando(false);
    }
  }

  if (isLoading) {
    return (
      <div className="facturas-panel">
        <p className="facturas-status">Cargando factura…</p>
      </div>
    );
  }

  if (!factura) {
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
        <div className="factura-campo">
          <label className="factura-detalle-label">Cliente</label>
          <div className="factura-campo-valor factura-campo-readonly">{factura.cliente}</div>
        </div>
        <div className="factura-campo">
          <label className="factura-detalle-label">RFC</label>
          <div className="factura-campo-valor factura-campo-readonly">{factura.rfc}</div>
        </div>
        <div className="factura-campo">
          <label className="factura-detalle-label">Fecha</label>
          <div className="factura-campo-valor factura-campo-readonly">{factura.fecha}</div>
        </div>
        <div className="factura-campo">
          <label className="factura-detalle-label">Subtotal</label>
          <div className="factura-campo-valor factura-campo-readonly factura-campo-mono">
            ${formatMonto(factura.subtotal)}
          </div>
        </div>
        <div className="factura-campo">
          <label className="factura-detalle-label">IVA</label>
          <div className="factura-campo-valor factura-campo-readonly factura-campo-mono">
            ${formatMonto(factura.iva)}
          </div>
        </div>
        <div className="factura-campo">
          <label className="factura-detalle-label">Total</label>
          <div className="factura-campo-valor factura-campo-readonly factura-campo-mono">
            ${formatMonto(factura.total)} {factura.moneda && factura.moneda !== "MXN" ? factura.moneda : ""}
          </div>
        </div>

        <Campo label="Número de OC" value={numeroOc} onChange={setNumeroOc} />
        <Campo label="Folio interno" value={folioInterno} onChange={setFolioInterno} />
        <div className="factura-campo">
          <label className="factura-detalle-label">Fecha de validación</label>
          <input
            className="factura-campo-input"
            type="date"
            value={fechaValidacion}
            onChange={(e) => setFechaValidacion(e.target.value)}
          />
        </div>
      </div>

      <div className="factura-detalle-oc">
        <label className="factura-detalle-label">Orden de compra vinculada</label>
        {factura.orden_compra ? (
          <div className="factura-oc-vinculada">
            <p style={{ margin: 0, fontWeight: 600, color: "var(--text-ink)" }}>
              {factura.orden_compra.numero_oc}
            </p>
            <p style={{ margin: "2px 0 0", fontSize: 12.5, color: "var(--text-muted)" }}>
              Recibida el {factura.orden_compra.fecha_recepcion.slice(0, 10)}
            </p>
          </div>
        ) : (
          <p className="facturas-status" style={{ padding: 0, textAlign: "left", margin: "0 0 10px" }}>
            Esta factura no tiene una orden de compra vinculada todavía.
          </p>
        )}

        {!mostrarCandidatas ? (
          <button className="factura-btn-secondary" onClick={handleVerCandidatas}>
            {factura.orden_compra ? "Cambiar vínculo con OC" : "Vincular orden de compra"}
          </button>
        ) : (
          <div className="factura-candidatas">
            {candidatas.length === 0 ? (
              <p className="facturas-status" style={{ padding: 0, textAlign: "left" }}>
                No hay órdenes de compra candidatas para esta factura.
              </p>
            ) : (
              candidatas.map((c) => (
                <div key={c.id} className="factura-candidata-card">
                  <div>
                    <p style={{ margin: "0 0 3px", fontWeight: 600, fontSize: 13.5, color: "var(--text-ink)" }}>
                      {c.numero_oc}
                    </p>
                    <p style={{ margin: 0, fontSize: 12, color: "var(--text-muted)" }}>
                      Recibida {c.fecha_recepcion.slice(0, 10)} · {c.facturas_asociadas}{" "}
                      {c.facturas_asociadas === 1 ? "factura asociada" : "facturas asociadas"}
                    </p>
                  </div>
                  <button
                    className="factura-btn-primary"
                    onClick={() => handleVincular(c.id)}
                    disabled={isVinculando}
                  >
                    Vincular
                  </button>
                </div>
              ))
            )}
            <span
              className="factura-volver"
              style={{ fontSize: 12.5 }}
              onClick={() => setMostrarCandidatas(false)}
            >
              Cancelar
            </span>
          </div>
        )}
      </div>

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
              <p className="factura-file-name">Ver factura</p>
              <p className="factura-file-action">Abrir documento</p>
            </div>
          </a>
        ) : (
          <p className="facturas-status" style={{ padding: 0, textAlign: "left" }}>
            Esta factura no tiene PDF adjunto.
          </p>
        )}
      </div>

      {guardadoOk && !error && (
        <p className="facturas-status" style={{ color: "#2e7d5b", textAlign: "left", padding: 0 }}>
          Cambios guardados ✓
        </p>
      )}
      {error && <p className="facturas-status facturas-status-error">{error}</p>}

      <div className="factura-detalle-actions">
        <button className="cancelar-btn" onClick={() => setMostrarCancelar(true)}>
          Cancelar factura
        </button>
        <button className="factura-btn-secondary" onClick={handleReporteDetalle} disabled={descargando}>
          {descargando ? "Generando…" : "Reporte detalle"}
        </button>
        <button className="factura-btn-primary" onClick={handleGuardar} disabled={isSaving}>
          {isSaving ? "Guardando…" : "Guardar cambios"}
        </button>
      </div>

      {mostrarCancelar && (
        <CancelarModal
          titulo="¿Cancelar esta factura?"
          onCerrar={() => setMostrarCancelar(false)}
          onConfirmar={handleCancelar}
        />
      )}
    </div>
  );
}

function Campo({
  label,
  value,
  onChange,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
}) {
  return (
    <div className="factura-campo">
      <label className="factura-detalle-label">{label}</label>
      <input
        className="factura-campo-input"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder="—"
      />
    </div>
  );
}