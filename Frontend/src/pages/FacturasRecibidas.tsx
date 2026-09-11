import { useEffect, useState } from "react";
import {
  listarFacturasRecibidas,
  listarEmisores,
  obtenerFacturaRecibida,
  sincronizarRecibidas,
  descargarReporteRecibidas,
  type FacturaRecibidaListado,
  type FacturaRecibidaDetalle,
  type ResumenFacturasRecibidas,
  type EmisorOpcion,
  type FiltrosRecibidas,
} from "../api/facturas-recibidas";
import { ApiError } from "../api/client";
import "./Facturas.css";

function formatMonto(valor?: string | null) {
  if (!valor) return "—";
  const num = Number(valor);
  if (Number.isNaN(num)) return "—";
  return num.toLocaleString("es-MX", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
}

function EstadoSatBadge({ estado }: { estado: string }) {
  const cancelado = estado.toLowerCase().includes("cancel");
  return (
    <span
      className="factura-badge"
      style={
        cancelado
          ? { background: "#fbe7e7", color: "#a33b3b" }
          : { background: "#e5f0e8", color: "#2e7d5b" }
      }
    >
      {estado}
    </span>
  );
}

export function FacturasRecibidas() {
  const [facturas, setFacturas] = useState<FacturaRecibidaListado[]>([]);
  const [resumen, setResumen] = useState<ResumenFacturasRecibidas | null>(null);
  const [emisores, setEmisores] = useState<EmisorOpcion[]>([]);
  const [pagina, setPagina] = useState(1);
  const [totalPaginas, setTotalPaginas] = useState(1);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [idSeleccionado, setIdSeleccionado] = useState<number | null>(null);
  const [mostrarSincronizar, setMostrarSincronizar] = useState(false);

  const [q, setQ] = useState("");
  const [rfcEmisor, setRfcEmisor] = useState("");
  const [satEstado, setSatEstado] = useState<"" | "Vigente" | "Cancelado">("");
  const [soloFacturas, setSoloFacturas] = useState(true);
  const [fechaDesde, setFechaDesde] = useState("");
  const [fechaHasta, setFechaHasta] = useState("");

  const [descargandoReporte, setDescargandoReporte] = useState(false);

  useEffect(() => {
    listarEmisores().then(setEmisores).catch(() => {});
  }, []);

  useEffect(() => {
    const id = setTimeout(() => cargar(1), 400);
    return () => clearTimeout(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [q, rfcEmisor, satEstado, soloFacturas, fechaDesde, fechaHasta]);

  function filtrosActuales(paginaOverride?: number): FiltrosRecibidas {
    return {
      q: q || undefined,
      rfc_emisor: rfcEmisor || undefined,
      sat_estado: satEstado || undefined,
      solo_facturas: soloFacturas,
      fecha_desde: fechaDesde || undefined,
      fecha_hasta: fechaHasta || undefined,
      pagina: paginaOverride ?? pagina,
      por_pagina: 50,
    };
  }

  function cargar(paginaObjetivo: number) {
    setIsLoading(true);
    setError(null);
    listarFacturasRecibidas(filtrosActuales(paginaObjetivo))
      .then((res) => {
        setFacturas(res.facturas);
        setResumen(res.resumen);
        setPagina(res.pagina);
        setTotalPaginas(res.total_paginas);
      })
      .catch((err) => {
        setError(
          err instanceof ApiError
            ? err.message
            : "No se pudieron cargar las facturas recibidas."
        );
      })
      .finally(() => setIsLoading(false));
  }

  async function handleReporteGeneral() {
    setDescargandoReporte(true);
    try {
      await descargarReporteRecibidas(filtrosActuales());
    } catch {
      setError("No se pudo generar el reporte.");
    } finally {
      setDescargandoReporte(false);
    }
  }

  if (idSeleccionado !== null) {
    return (
      <FacturaRecibidaDetalleView
        id={idSeleccionado}
        onVolver={() => setIdSeleccionado(null)}
      />
    );
  }

  return (
    <div className="facturas-panel">
      <div className="facturas-header">
        <div>
        <p className="facturas-eyebrow">Del SAT · Descarga Masiva</p>
        <h2 className="facturas-title">Facturas emitidas</h2>
        </div>
        <div style={{ display: "flex", gap: 10 }}>
          <button
            className="factura-btn-secondary"
            onClick={() => setMostrarSincronizar(true)}
          >
            Sincronizar con SAT
          </button>
          <button
            className="factura-btn-primary"
            onClick={handleReporteGeneral}
            disabled={descargandoReporte}
          >
            {descargandoReporte ? "Generando…" : "Reporte PDF"}
          </button>
        </div>
      </div>

      <p className="facturas-nota-historico">
        Este listado es de solo lectura — proviene directamente del SAT. Los datos no
        se pueden editar, vincular ni cancelar desde aquí; cualquier corrección se
        hace ante el SAT.
      </p>

      <div className="facturas-filtros">
        <input
          className="field-input facturas-buscador"
          placeholder="Buscar por folio fiscal, RFC o nombre del emisor…"
          value={q}
          onChange={(e) => setQ(e.target.value)}
        />

        <div className="facturas-filtros-grid">
          <select
            className="field-input"
            value={rfcEmisor}
            onChange={(e) => setRfcEmisor(e.target.value)}
          >
            <option value="">Todos los proveedores</option>
            {emisores.map((em) => (
              <option key={em.rfc_emisor} value={em.rfc_emisor}>
                {em.nombre_emisor} ({em.total_facturas})
              </option>
            ))}
          </select>
          <select
            className="field-input"
            value={satEstado}
            onChange={(e) => setSatEstado(e.target.value as typeof satEstado)}
          >
            <option value="">Vigentes y canceladas</option>
            <option value="Vigente">Solo vigentes</option>
            <option value="Cancelado">Solo canceladas</option>
          </select>
        </div>

        <div className="facturas-rango-fechas">
          <div className="facturas-rango-campo">
            <label className="facturas-rango-label">Desde</label>
            <input
              className="field-input"
              type="date"
              value={fechaDesde}
              onChange={(e) => setFechaDesde(e.target.value)}
            />
          </div>
          <div className="facturas-rango-campo">
            <label className="facturas-rango-label">Hasta</label>
            <input
              className="field-input"
              type="date"
              value={fechaHasta}
              onChange={(e) => setFechaHasta(e.target.value)}
            />
          </div>
          <label className="facturas-checkbox facturas-checkbox-inline">
            <input
              type="checkbox"
              checked={soloFacturas}
              onChange={(e) => setSoloFacturas(e.target.checked)}
            />
            Solo facturas (excluir notas de crédito y otros)
          </label>
        </div>
      </div>

      {resumen && (
        <div className="facturas-resumen">
          <div>
            <p className="facturas-resumen-label">Facturas</p>
            <p className="facturas-resumen-valor">{resumen.total_facturas}</p>
          </div>
          <div>
            <p className="facturas-resumen-label">Monto total</p>
            <p className="facturas-resumen-valor">${formatMonto(resumen.total_monto)}</p>
          </div>
          <div>
            <p className="facturas-resumen-label">Vigentes</p>
            <p className="facturas-resumen-valor">{resumen.total_vigentes}</p>
          </div>
          <div>
            <p className="facturas-resumen-label">Canceladas</p>
            <p className="facturas-resumen-valor">{resumen.total_canceladas}</p>
          </div>
          <div>
            <p className="facturas-resumen-label">Proveedores</p>
            <p className="facturas-resumen-valor">{resumen.total_emisores}</p>
          </div>
        </div>
      )}

      <p className="facturas-nota-historico" style={{ borderTop: "none" }}>
        Nota: el SAT no reporta moneda en estos comprobantes — los montos se muestran
        tal cual, sin asumir MXN.
      </p>

      {isLoading && <p className="facturas-status">Cargando facturas…</p>}
      {error && <p className="facturas-status facturas-status-error">{error}</p>}

      {!isLoading && !error && (
        <>
          <table className="facturas-table">
            <thead>
              <tr>
                <th>Folio fiscal</th>
                <th>Proveedor</th>
                <th>Fecha</th>
                <th>Monto</th>
                <th>Estatus SAT</th>
              </tr>
            </thead>
            <tbody>
              {facturas.map((f) => (
                <tr
                  key={f.id_factura_recibida}
                  className={`facturas-row${
                    f.sat_estado.toLowerCase().includes("cancel")
                      ? " facturas-row-cancelada"
                      : ""
                  }`}
                  onClick={() => setIdSeleccionado(f.id_factura_recibida)}
                >
                  <td
                    className="facturas-cell-strong"
                    style={{ fontFamily: "var(--font-mono)", fontSize: 12 }}
                  >
                    {f.folio_fiscal.slice(0, 8)}…
                  </td>
                  <td>{f.nombre_emisor}</td>
                  <td className="facturas-cell-muted">
                    {f.fecha_emision.slice(0, 10)}
                  </td>
                  <td className="facturas-cell-mono">${formatMonto(f.monto_total)}</td>
                  <td>
                    <EstadoSatBadge estado={f.sat_estado} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>

          {facturas.length === 0 && (
            <p className="facturas-status">
              No hay facturas recibidas que coincidan con los filtros.
            </p>
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

      {mostrarSincronizar && (
        <SincronizarModal onCerrar={() => setMostrarSincronizar(false)} />
      )}
    </div>
  );
}

function SincronizarModal({ onCerrar }: { onCerrar: () => void }) {
  const [fechaInicial, setFechaInicial] = useState("");
  const [fechaFinal, setFechaFinal] = useState("");
  const [isLoading, setIsLoading] = useState(false);
  const [mensaje, setMensaje] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function handleSincronizar() {
    setIsLoading(true);
    setError(null);
    setMensaje(null);
    try {
      await sincronizarRecibidas({
        fecha_inicial: fechaInicial,
        fecha_final: fechaFinal,
      });
      setMensaje(
        "Solicitud registrada. El SAT puede tardar de minutos a horas en responder — los datos aparecerán aquí cuando estén listos."
      );
    } catch (err) {
      setError(
        err instanceof ApiError ? err.message : "No se pudo registrar la solicitud."
      );
    } finally {
      setIsLoading(false);
    }
  }

  return (
    <div className="cancelar-modal-overlay" onClick={onCerrar}>
      <div className="cancelar-modal" onClick={(e) => e.stopPropagation()}>
        <h3 className="cancelar-modal-title">Sincronizar con el SAT</h3>
        <p className="cancelar-modal-warning">
          Esto dispara una descarga manual para el rango que elijas. No es
          inmediato — el SAT procesa la solicitud de forma asíncrona.
        </p>

        <div style={{ display: "flex", gap: 10, marginBottom: 14 }}>
          <label className="field" style={{ flex: 1 }}>
            <span className="field-label">Fecha inicial</span>
            <input
              className="field-input"
              type="date"
              value={fechaInicial}
              onChange={(e) => setFechaInicial(e.target.value)}
            />
          </label>
          <label className="field" style={{ flex: 1 }}>
            <span className="field-label">Fecha final</span>
            <input
              className="field-input"
              type="date"
              value={fechaFinal}
              onChange={(e) => setFechaFinal(e.target.value)}
            />
          </label>
        </div>

        {mensaje && (
          <p
            className="facturas-status"
            style={{ color: "#2e7d5b", padding: 0, textAlign: "left", marginBottom: 10 }}
          >
            {mensaje}
          </p>
        )}
        {error && (
          <p
            className="facturas-status facturas-status-error"
            style={{ padding: 0, textAlign: "left", marginBottom: 10 }}
          >
            {error}
          </p>
        )}

        <div className="cancelar-modal-actions">
          <button className="factura-btn-secondary" onClick={onCerrar}>
            Cerrar
          </button>
          <button
            className="factura-btn-primary"
            onClick={handleSincronizar}
            disabled={isLoading || !fechaInicial || !fechaFinal}
          >
            {isLoading ? "Enviando…" : "Sincronizar"}
          </button>
        </div>
      </div>
    </div>
  );
}

function FacturaRecibidaDetalleView({
  id,
  onVolver,
}: {
  id: number;
  onVolver: () => void;
}) {
  const [factura, setFactura] = useState<FacturaRecibidaDetalle | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    obtenerFacturaRecibida(id)
      .then(setFactura)
      .catch((err) => {
        setError(
          err instanceof ApiError ? err.message : "No se pudo cargar la factura."
        );
      })
      .finally(() => setIsLoading(false));
  }, [id]);

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
        <h2 className="factura-detalle-title">
          Factura de {factura.nombre_emisor}
        </h2>
        <span className="factura-detalle-badge-wrap">
          <EstadoSatBadge estado={factura.sat_estado} />
        </span>
      </div>

      <div className="factura-detalle-grid">
        <div className="factura-campo factura-campo-full">
          <label className="factura-detalle-label">Folio fiscal (UUID)</label>
          <div
            className="factura-campo-valor factura-campo-readonly factura-campo-mono"
            style={{ fontSize: 12 }}
          >
            {factura.folio_fiscal}
          </div>
        </div>
        <div className="factura-campo">
          <label className="factura-detalle-label">RFC emisor</label>
          <div className="factura-campo-valor factura-campo-readonly factura-campo-mono">
            {factura.rfc_emisor}
          </div>
        </div>
        <div className="factura-campo">
          <label className="factura-detalle-label">Nombre emisor</label>
          <div className="factura-campo-valor factura-campo-readonly">
            {factura.nombre_emisor}
          </div>
        </div>
        <div className="factura-campo">
          <label className="factura-detalle-label">Fecha de emisión</label>
          <div className="factura-campo-valor factura-campo-readonly">
            {factura.fecha_emision.replace("T", " ").slice(0, 16)}
          </div>
        </div>
        <div className="factura-campo">
          <label className="factura-detalle-label">Monto</label>
          <div className="factura-campo-valor factura-campo-readonly factura-campo-mono">
            ${formatMonto(factura.monto_total)}
          </div>
        </div>
        <div className="factura-campo">
          <label className="factura-detalle-label">Tipo de comprobante</label>
          <div className="factura-campo-valor factura-campo-readonly">
            {factura.efecto_comprobante}
          </div>
        </div>
        <div className="factura-campo">
          <label className="factura-detalle-label">Origen</label>
          <div className="factura-campo-valor factura-campo-readonly">
            {factura.origen}
          </div>
        </div>
      </div>

      <div className="factura-detalle-sat">
        <label className="factura-detalle-label">Verificación ante el SAT</label>
        <div className="factura-sat-resultado">
          <div className="factura-sat-fila">
            <span className="factura-sat-etiqueta">¿Es cancelable?</span>
            <span className="factura-sat-valor">
              {factura.sat_es_cancelable ?? "Sin verificar aún"}
            </span>
          </div>
          <div className="factura-sat-fila">
            <span className="factura-sat-etiqueta">Estatus de cancelación</span>
            <span className="factura-sat-valor">
              {factura.sat_estatus_cancelacion ?? "—"}
            </span>
          </div>
          <div className="factura-sat-fila">
            <span className="factura-sat-etiqueta">Código de estatus</span>
            <span className="factura-sat-valor">
              {factura.sat_codigo_estatus ?? "Sin verificar aún"}
            </span>
          </div>
          <div className="factura-sat-fila">
            <span className="factura-sat-etiqueta">Última verificación</span>
            <span className="factura-sat-valor">
              {factura.fecha_ultima_verificacion_sat
                ? factura.fecha_ultima_verificacion_sat.replace("T", " ").slice(0, 16)
                : "—"}
            </span>
          </div>
        </div>
      </div>

      <p
        className="facturas-status"
        style={{ padding: "0 24px 20px", textAlign: "left" }}
      >
        Esta factura no tiene PDF ni XML descargables — el SAT solo entrega metadata
        por este medio.
      </p>
    </div>
  );
}