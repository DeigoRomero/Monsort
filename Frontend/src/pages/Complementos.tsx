import { useEffect, useState } from "react";
import {
  listarComplementos,
  listarHuerfanos,
  obtenerComplemento,
  reconciliarComplementos,
  abrirPdfComplemento,
  descargarXmlComplemento,
  type ComplementoListado,
  type ComplementoDetalle,
  type ResumenComplementos,
  type DocumentoHuerfano,
  type FiltrosComplemento,
} from "../api/complementos";
import { ArchivoLink } from "../Components/ArchivoLink";
import { BotonFiltros, Plegable, VistaAnimada } from "../Components/Animaciones";
import { useSalida } from "../Components/movimiento";
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

function formatFecha(valor?: string | null) {
  if (!valor) return "—";
  return valor.slice(0, 10);
}

function uuidCorto(uuid: string) {
  return uuid.slice(0, 8) + "…";
}

export function Complementos() {
  const [tab, setTab] = useState<"listado" | "huerfanos">("listado");

  return (
    <div className="facturas-panel">
      <div className="facturas-header facturas-header-tabs">
        <div>
          <p className="facturas-eyebrow">Pagos recibidos</p>
          <h2 className="facturas-title">Complementos de pago</h2>
        </div>
      </div>

      <div className="facturas-tabs">
        <span
          className={`facturas-tab${tab === "listado" ? " active" : ""}`}
          onClick={() => setTab("listado")}
        >
          Todos los complementos
        </span>
        <span
          className={`facturas-tab${tab === "huerfanos" ? " active" : ""}`}
          onClick={() => setTab("huerfanos")}
        >
          Pagos sin factura
        </span>
      </div>

      {tab === "listado" ? <ListadoComplementos /> : <VistaHuerfanos />}
    </div>
  );
}

function ListadoComplementos() {
  const [complementos, setComplementos] = useState<ComplementoListado[]>([]);
  const [resumen, setResumen] = useState<ResumenComplementos | null>(null);
  const [pagina, setPagina] = useState(1);
  const [totalPaginas, setTotalPaginas] = useState(1);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [idSeleccionado, setIdSeleccionado] = useState<number | null>(null);
  const [filtrosAbiertos, setFiltrosAbiertos] = useState(false);
  const { saliendo, salir } = useSalida();

  const [q, setQ] = useState("");
  const [formaPago, setFormaPago] = useState("");
  const [vinculado, setVinculado] = useState<"todos" | "true" | "false">("todos");
  const [soloAtencion, setSoloAtencion] = useState(false);
  const [incluirCancelados, setIncluirCancelados] = useState(false);
  const [fechaDesde, setFechaDesde] = useState("");
  const [fechaHasta, setFechaHasta] = useState("");

  const [reconciliando, setReconciliando] = useState(false);
  const [mensajeReconciliar, setMensajeReconciliar] = useState<string | null>(null);

  useEffect(() => {
    const id = setTimeout(() => cargar(1), 400);
    return () => clearTimeout(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [q, formaPago, vinculado, soloAtencion, incluirCancelados, fechaDesde, fechaHasta]);

  function filtrosActuales(paginaOverride?: number): FiltrosComplemento {
    return {
      q: q || undefined,
      forma_pago: formaPago || undefined,
      vinculado: vinculado === "todos" ? undefined : vinculado === "true",
      solo_atencion: soloAtencion,
      incluir_cancelados: incluirCancelados,
      fecha_desde: fechaDesde || undefined,
      fecha_hasta: fechaHasta || undefined,
      pagina: paginaOverride ?? pagina,
      por_pagina: 50,
    };
  }

  function cargar(paginaObjetivo: number) {
    setIsLoading(true);
    setError(null);
    listarComplementos(filtrosActuales(paginaObjetivo))
      .then((res) => {
        setComplementos(res.complementos);
        setResumen(res.resumen);
        setPagina(res.pagina);
        setTotalPaginas(res.total_paginas);
      })
      .catch((err) => {
        setError(
          err instanceof ApiError
            ? err.message
            : "No se pudieron cargar los complementos de pago."
        );
      })
      .finally(() => setIsLoading(false));
  }

  async function handleReconciliar() {
    setReconciliando(true);
    setMensajeReconciliar(null);
    try {
      await reconciliarComplementos();
      setMensajeReconciliar("Reconciliación ejecutada. Listado actualizado.");
      cargar(pagina);
    } catch (err) {
      setError(
        err instanceof ApiError ? err.message : "No se pudo reconciliar."
      );
    } finally {
      setReconciliando(false);
    }
  }

  const filtrosActivos = [formaPago, fechaDesde, fechaHasta].filter(Boolean).length
    + (vinculado !== "todos" ? 1 : 0) + (soloAtencion ? 1 : 0) + (incluirCancelados ? 1 : 0);

  if (idSeleccionado !== null) {
    return (
      <VistaAnimada key={`detalle-${idSeleccionado}`} tipo="detalle" saliendo={saliendo}>
        <ComplementoDetalleView
          id={idSeleccionado}
          onVolver={() =>
            salir(() => {
              setIdSeleccionado(null);
              cargar(pagina);
            })
          }
        />
      </VistaAnimada>
    );
  }

  return (
    <VistaAnimada key="listado" tipo="listado" saliendo={saliendo}>
      <div className="facturas-filtros">
        <div className="filtros-barra">
          <input
            className="field-input facturas-buscador"
            placeholder="Buscar por UUID del complemento, folio o UUID de la factura…"
            value={q}
            onChange={(e) => setQ(e.target.value)}
          />
          <BotonFiltros
            abierto={filtrosAbiertos}
            activos={filtrosActivos}
            onClick={() => setFiltrosAbiertos((v) => !v)}
            controla="filtros-complementos"
          />
        </div>

        <Plegable abierto={filtrosAbiertos} id="filtros-complementos">
        <div className="filtros-plegables">
        <div className="facturas-filtros-grid">
          <input
            className="field-input"
            placeholder="Forma de pago (ej. 03)"
            value={formaPago}
            onChange={(e) => setFormaPago(e.target.value)}
          />
          <select
            className="field-input"
            value={vinculado}
            onChange={(e) => setVinculado(e.target.value as typeof vinculado)}
          >
            <option value="todos">Vinculados y sin vincular</option>
            <option value="true">Solo totalmente vinculados</option>
            <option value="false">Solo con pagos sin vincular</option>
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
              checked={soloAtencion}
              onChange={(e) => setSoloAtencion(e.target.checked)}
            />
            Solo los que requieren atención
          </label>
          <label className="facturas-checkbox facturas-checkbox-inline">
            <input
              type="checkbox"
              checked={incluirCancelados}
              onChange={(e) => setIncluirCancelados(e.target.checked)}
            />
            Incluir cancelados
          </label>
        </div>
        </div>
        </Plegable>
      </div>

      {resumen && (
        <div className="facturas-resumen">
          <div>
            <p className="facturas-resumen-label">Complementos</p>
            <p className="facturas-resumen-valor">{resumen.total_complementos}</p>
          </div>
          <div>
            <p className="facturas-resumen-label">Monto total</p>
            <p className="facturas-resumen-valor">${formatMonto(resumen.total_monto)}</p>
          </div>
          <div>
            <p className="facturas-resumen-label">Vinculados</p>
            <p className="facturas-resumen-valor">{resumen.totalmente_vinculados}</p>
          </div>
          <div>
            <p className="facturas-resumen-label">Con huérfanos</p>
            <p className="facturas-resumen-valor">{resumen.con_huerfanos}</p>
          </div>
          <div>
            <p className="facturas-resumen-label">Sin PDF</p>
            <p className="facturas-resumen-valor">{resumen.sin_pdf}</p>
          </div>
        </div>
      )}

      <div
        style={{
          padding: "14px 28px",
          borderBottom: "1px solid var(--hairline)",
          display: "flex",
          alignItems: "center",
          gap: 14,
        }}
      >
        <button
          className="factura-btn-secondary"
          onClick={handleReconciliar}
          disabled={reconciliando}
        >
          {reconciliando ? "Reconciliando…" : "Reintentar vinculación"}
        </button>
        {mensajeReconciliar && (
          <span style={{ fontSize: 12.5, color: "#2e7d5b" }}>
            {mensajeReconciliar}
          </span>
        )}
      </div>

      {isLoading && <p className="facturas-status">Cargando complementos…</p>}
      {error && <p className="facturas-status facturas-status-error">{error}</p>}

      {!isLoading && !error && (
        <>
          <table className="facturas-table">
            <thead>
              <tr>
                <th>Folio CP</th>
                <th>Fecha de pago</th>
                <th>Monto</th>
                <th>Forma</th>
                <th>Facturas</th>
                <th>Alerta</th>
              </tr>
            </thead>
            <tbody>
              {complementos.map((c) => (
                <tr
                  key={c.id}
                  className={`facturas-row${c.cancelado ? " facturas-row-cancelada" : ""}`}
                  onClick={() => salir(() => setIdSeleccionado(c.id))}
                >
                  <td className="facturas-cell-strong">
                    {c.folio ?? uuidCorto(c.uuid_cp)}
                  </td>
                  <td className="facturas-cell-muted">{formatFecha(c.fecha_pago)}</td>
                  <td className="facturas-cell-mono">
                    ${formatMonto(c.monto)}{" "}
                    {c.moneda && c.moneda !== "MXN" ? c.moneda : ""}
                  </td>
                  <td className="facturas-cell-muted">{c.forma_pago ?? "—"}</td>
                  <td>
                    {c.documentos_total > 0 ? (
                      <span
                        style={{
                          fontSize: 12.5,
                          color:
                            c.documentos_huerfanos > 0
                              ? "#a33b3b"
                              : "var(--text-muted)",
                        }}
                      >
                        {c.documentos_vinculados} de {c.documentos_total}
                      </span>
                    ) : (
                      <span style={{ color: "#c4cad6", fontSize: 12 }}>—</span>
                    )}
                  </td>
                  <td>
                    {c.requiere_atencion ? (
                      <span
                        className="factura-badge"
                        style={{ background: "#fdf1de", color: "#8a6d1f" }}
                        title={c.motivo_atencion ?? ""}
                      >
                        {c.motivo_atencion ?? "Requiere atención"}
                      </span>
                    ) : (
                      <span
                        className="factura-badge"
                        style={{ background: "#e5f0e8", color: "#2e7d5b" }}
                      >
                        Completo
                      </span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>

          {complementos.length === 0 && (
            <p className="facturas-status">
              No hay complementos de pago que coincidan con los filtros.
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
    </VistaAnimada>
  );
}

function ComplementoDetalleView({
  id,
  onVolver,
}: {
  id: number;
  onVolver: () => void;
}) {
  const [cp, setCp] = useState<ComplementoDetalle | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    obtenerComplemento(id)
      .then(setCp)
      .catch((err) => {
        setError(
          err instanceof ApiError
            ? err.message
            : "No se pudo cargar el complemento."
        );
      })
      .finally(() => setIsLoading(false));
  }, [id]);

  if (isLoading) {
    return <p className="facturas-status">Cargando complemento…</p>;
  }

  if (!cp) {
    return (
      <p className="facturas-status facturas-status-error">
        {error ?? "No se encontró el complemento."}
      </p>
    );
  }

  return (
    <div>
      <div className="factura-detalle-header">
        <span className="factura-volver" onClick={onVolver}>
          ← Volver
        </span>
        <span className="factura-detalle-divider">|</span>
        <h2 className="factura-detalle-title">
          Complemento {cp.folio ?? uuidCorto(cp.uuid_cp)}
        </h2>
        {cp.cancelado && (
          <span
            className="factura-badge"
            style={{ background: "#eef0f3", color: "#8a92a5", marginLeft: "auto" }}
          >
            Cancelado
          </span>
        )}
      </div>

      {cp.requiere_atencion && cp.motivo_atencion && (
        <p className="facturas-nota-historico">{cp.motivo_atencion}</p>
      )}

      <div className="factura-detalle-grid">
        <div className="factura-campo factura-campo-full">
          <label className="factura-detalle-label">UUID del complemento</label>
          <div
            className="factura-campo-valor factura-campo-readonly factura-campo-mono"
            style={{ fontSize: 12 }}
          >
            {cp.uuid_cp}
          </div>
        </div>
        <div className="factura-campo">
          <label className="factura-detalle-label">Fecha de pago</label>
          <div className="factura-campo-valor factura-campo-readonly">
            {formatFecha(cp.fecha_pago)}
          </div>
        </div>
        <div className="factura-campo">
          <label className="factura-detalle-label">Fecha de recepción</label>
          <div className="factura-campo-valor factura-campo-readonly">
            {formatFecha(cp.fecha_recepcion)}
          </div>
        </div>
        <div className="factura-campo">
          <label className="factura-detalle-label">Monto</label>
          <div className="factura-campo-valor factura-campo-readonly factura-campo-mono">
            ${formatMonto(cp.monto)} {cp.moneda ?? ""}
          </div>
        </div>
        <div className="factura-campo">
          <label className="factura-detalle-label">Forma de pago</label>
          <div className="factura-campo-valor factura-campo-readonly">
            {cp.forma_pago ?? "—"}
          </div>
        </div>
        {cp.cancelado && (
          <>
            <div className="factura-campo">
              <label className="factura-detalle-label">Fecha de cancelación</label>
              <div className="factura-campo-valor factura-campo-readonly">
                {formatFecha(cp.fecha_cancelacion)}
              </div>
            </div>
            <div className="factura-campo">
              <label className="factura-detalle-label">Motivo</label>
              <div className="factura-campo-valor factura-campo-readonly">
                {cp.motivo_cancelacion ?? "—"}
              </div>
            </div>
          </>
        )}
      </div>

      <div className="factura-conceptos" style={{ padding: "0 28px 20px" }}>
        <label className="factura-detalle-label">Documentos relacionados</label>
        {cp.documentos.length === 0 ? (
          <p
            className="facturas-status"
            style={{ padding: "10px 0 0", textAlign: "left" }}
          >
            Este complemento no trae documentos relacionados.
          </p>
        ) : (
          <table className="factura-conceptos-table">
            <thead>
              <tr>
                <th>Factura</th>
                <th>Parcialidad</th>
                <th>Pagado</th>
                <th>Saldo</th>
                <th>Estado</th>
              </tr>
            </thead>
            <tbody>
              {cp.documentos.map((d, i) => (
                <tr key={i}>
                  <td>
                    {d.factura?.vinculada ? (
                      <span>
                        {d.factura.folio_interno ?? uuidCorto(d.factura.folio_fiscal)}
                        {d.factura.cliente && (
                          <span
                            style={{
                              color: "var(--text-muted)",
                              marginLeft: 6,
                              fontSize: 11.5,
                            }}
                          >
                            {d.factura.cliente}
                          </span>
                        )}
                      </span>
                    ) : (
                      <span
                        style={{
                          fontFamily: "var(--font-mono)",
                          fontSize: 11.5,
                          color: "#a33b3b",
                        }}
                        title={d.uuid_documento}
                      >
                        {uuidCorto(d.uuid_documento)} · sin vincular
                      </span>
                    )}
                  </td>
                  <td>{d.num_parcialidad ?? "—"}</td>
                  <td className="facturas-cell-mono">${formatMonto(d.imp_pagado)}</td>
                  <td className="facturas-cell-mono">
                    ${formatMonto(d.imp_saldo_insoluto)}
                  </td>
                  <td>
                    {d.liquida ? (
                      <span
                        className="factura-badge"
                        style={{ background: "#e5f0e8", color: "#2e7d5b" }}
                      >
                        Liquidada
                      </span>
                    ) : (
                      <span
                        className="factura-badge"
                        style={{ background: "#fdf1de", color: "#8a6d1f" }}
                      >
                        Parcial
                      </span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {(cp.tiene_pdf || cp.tiene_xml) && (
        <div className="factura-detalle-archivo" style={{ padding: "0 28px 24px" }}>
          <label className="factura-detalle-label">Archivos</label>
          <div style={{ display: "flex", gap: 12 }}>
            {cp.tiene_pdf && (
              <ArchivoLink
                className="factura-file-card"
                accion={() => abrirPdfComplemento(cp.id)}
              >
                <span className="factura-file-icon">PDF</span>
                <div>
                  <p className="factura-file-name">Ver complemento</p>
                  <p className="factura-file-action">Abrir documento</p>
                </div>
              </ArchivoLink>
            )}
            {cp.tiene_xml && (
              <ArchivoLink
                className="factura-file-card"
                accion={() => descargarXmlComplemento(cp.id)}
              >
                <span className="factura-file-icon">XML</span>
                <div>
                  <p className="factura-file-name">Descargar XML</p>
                  <p className="factura-file-action">Archivo original</p>
                </div>
              </ArchivoLink>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

function VistaHuerfanos() {
  const [huerfanos, setHuerfanos] = useState<DocumentoHuerfano[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    listarHuerfanos()
      .then(setHuerfanos)
      .catch((err) => {
        setError(
          err instanceof ApiError
            ? err.message
            : "No se pudieron cargar los pagos sin factura."
        );
      })
      .finally(() => setIsLoading(false));
  }, []);

  return (
    <>
      <p className="facturas-nota-historico">
        Estos son pagos que el cliente recibió pero que el sistema no pudo ligar a
        ninguna factura. Cada renglón explica el motivo.
      </p>

      {isLoading && <p className="facturas-status">Cargando…</p>}
      {error && <p className="facturas-status facturas-status-error">{error}</p>}

      {!isLoading && !error && (
        <>
          <table className="facturas-table">
            <thead>
              <tr>
                <th>Folio CP</th>
                <th>Fecha de pago</th>
                <th>UUID de la factura</th>
                <th>Pagado</th>
                <th>Motivo</th>
              </tr>
            </thead>
            <tbody>
              {huerfanos.map((h, i) => (
                <tr key={`${h.id_complemento}-${i}`}>
                  <td className="facturas-cell-strong">
                    {h.folio_cp ?? uuidCorto(h.uuid_cp)}
                  </td>
                  <td className="facturas-cell-muted">{formatFecha(h.fecha_pago)}</td>
                  <td
                    style={{ fontFamily: "var(--font-mono)", fontSize: 11.5 }}
                    title={h.uuid_documento}
                  >
                    {uuidCorto(h.uuid_documento)}
                  </td>
                  <td className="facturas-cell-mono">${formatMonto(h.imp_pagado)}</td>
                  <td style={{ fontSize: 12.5, color: "var(--text-muted)" }}>
                    {h.motivo}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>

          {huerfanos.length === 0 && (
            <p className="facturas-status">
              No hay pagos sin factura vinculada. Todo está conciliado.
            </p>
          )}
        </>
      )}
    </>
  );
}