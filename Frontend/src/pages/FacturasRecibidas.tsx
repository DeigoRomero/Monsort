import { useEffect, useState } from "react";
import {
  listarFacturasRecibidas,
  listarEmisores,
  obtenerFacturaRecibida,
  obtenerEstadoSincronizacion,
  listarHistorialSincronizacion,
  iniciarSincronizacionPortal,
  descargarReporteRecibidas,
  urlXmlRecibida,
  type FacturaRecibidaListado,
  type FacturaRecibidaDetalle,
  type ResumenFacturasRecibidas,
  type EmisorOpcion,
  type FiltrosRecibidas,
  type EstadoSincronizacionPortal,
  type CorridaPortal,
} from "../api/facturas-recibidas";
import { ApiError } from "../api/client";
import "./Facturas.css";
import { useAuth } from "../context/AuthContext";

function formatMonto(valor?: string | null) {
  if (!valor) return "—";
  const num = Number(valor);
  if (Number.isNaN(num)) return "—";
  return num.toLocaleString("es-MX", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
}

/** "hoy 13:02" / "ayer 18:07" / "24 sep 09:07" */
function formatSincronizacion(iso: string | null) {
  if (!iso) return "todavía no se ha sincronizado";

  const fecha = new Date(iso);
  if (Number.isNaN(fecha.getTime())) return "fecha no disponible";

  const hora = fecha.toLocaleTimeString("es-MX", {
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  });

  const hoy = new Date();
  const ayer = new Date();
  ayer.setDate(hoy.getDate() - 1);

  const mismoDia = (a: Date, b: Date) =>
    a.getFullYear() === b.getFullYear() &&
    a.getMonth() === b.getMonth() &&
    a.getDate() === b.getDate();

  if (mismoDia(fecha, hoy)) return `hoy ${hora}`;
  if (mismoDia(fecha, ayer)) return `ayer ${hora}`;

  const dia = fecha.toLocaleDateString("es-MX", { day: "numeric", month: "short" });
  return `${dia} ${hora}`;
}

function EstadoSatBadge({ estado }: { estado: string | null }) {
  // Sin estado no se puede afirmar "Vigente": se dice que no se ha
  // consultado, que es lo que realmente pasa.
  if (!estado) {
    return (
      <span
        className="factura-badge"
        style={{ background: "#eef0f3", color: "#8a92a5" }}
      >
        Sin consultar
      </span>
    );
  }

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

function EncabezadoSincronizacion({
  estado,
}: {
  estado: EstadoSincronizacionPortal | null;
}) {
  if (!estado || !estado.activo) return null;

  return (
    <div className="recibidas-sync">
      <div className="recibidas-sync-linea">
        <span className="recibidas-sync-texto">
          {estado.en_curso ? (
            <>
              <span className="recibidas-sync-punto" />
              Sincronizando…
            </>
          ) : (
            <>
              Última sincronización con el SAT:{" "}
              <strong>
                {formatSincronizacion(estado.ultima_sincronizacion_exitosa)}
              </strong>
              {estado.nuevas_ultimas_24h > 0 && (
                <> · {estado.nuevas_ultimas_24h} nuevas en 24 h</>
              )}
            </>
          )}
        </span>
      </div>

      {estado.alerta && <p className="recibidas-sync-alerta">{estado.alerta}</p>}
    </div>
  );
}

export function FacturasRecibidas() {
  const [facturas, setFacturas] = useState<FacturaRecibidaListado[]>([]);
  const [resumen, setResumen] = useState<ResumenFacturasRecibidas | null>(null);
  const [emisores, setEmisores] = useState<EmisorOpcion[]>([]);
  const [sincronizacion, setSincronizacion] =
    useState<EstadoSincronizacionPortal | null>(null);
  const [pagina, setPagina] = useState(1);
  const [totalPaginas, setTotalPaginas] = useState(1);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [idSeleccionado, setIdSeleccionado] = useState<number | null>(null);
  const [mostrarHistorial, setMostrarHistorial] = useState(false);

  const [q, setQ] = useState("");
  const [rfcEmisor, setRfcEmisor] = useState("");
  const [satEstado, setSatEstado] = useState<"" | "Vigente" | "Cancelado">("");
  const [soloFacturas, setSoloFacturas] = useState(true);
  const [fechaDesde, setFechaDesde] = useState("");
  const [fechaHasta, setFechaHasta] = useState("");

  const [descargandoReporte, setDescargandoReporte] = useState(false);
  const [sincronizando, setSincronizando] = useState(false);
  const [errorSincronizacion, setErrorSincronizacion] = useState<string | null>(null);
  const { session } = useAuth();
  const rol = session?.usuario.rol?.toLowerCase() ?? "";
  const puedeSincronizar = ["desarrollador", "administrador"].includes(rol);
  
  useEffect(() => {
    listarEmisores().then(setEmisores).catch(() => {});
    obtenerEstadoSincronizacion().then(setSincronizacion).catch(() => {});
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

  async function handleSincronizarAhora() {
    setSincronizando(true);
    setErrorSincronizacion(null);
    try {
      await iniciarSincronizacionPortal(5);
      // el servidor respondio 202: la consulta sigue en segundo plano.
      // consultamos el estado cada 15s hasta que en_curso sea false.
      const intervalo = setInterval(async () => {
        try {
          const estado = await obtenerEstadoSincronizacion();
          setSincronizacion(estado);
          if (!estado.en_curso) {
            clearInterval(intervalo);
            setSincronizando(false);
            if (estado.ultima_corrida?.estado === "FALLIDA") {
              setErrorSincronizacion(
                estado.ultima_corrida.error ?? "La sincronización falló."
              );
            } else {
              cargar(1);
            }
          }
        } catch {
          clearInterval(intervalo);
          setSincronizando(false);
        }
      }, 15000);
    } catch (err) {
      setSincronizando(false);
      if (err instanceof ApiError) {
        setErrorSincronizacion(err.message);
      } else {
        setErrorSincronizacion("No se pudo iniciar la sincronización.");
      }
    }
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

  if (mostrarHistorial) {
    return <HistorialSincronizacion onVolver={() => setMostrarHistorial(false)} />;
  }

  return (
    <div className="facturas-panel">
      <div className="facturas-header">
        <div>
          <p className="facturas-eyebrow">Del SAT · Portal</p>
          <h2 className="facturas-title">Facturas recibidas</h2>
        </div>
        <div style={{ display: "flex", gap: 10 }}>
          {puedeSincronizar && (
            <button
              className="factura-btn-secondary"
              onClick={handleSincronizarAhora}
              disabled={sincronizando || sincronizacion?.en_curso}
            >
              {sincronizando || sincronizacion?.en_curso
                ? "Consultando al SAT…"
                : "Actualizar desde el SAT"}
            </button>
          )}
          <button
            className="factura-btn-secondary"
            onClick={() => setMostrarHistorial(true)}
          >
            Historial
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

      <EncabezadoSincronizacion estado={sincronizacion} />
      {errorSincronizacion && (
        <p className="facturas-status facturas-status-error" style={{ margin: 0 }}>
          {errorSincronizacion}
        </p>
      )}

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
                {em.nombre_emisor ?? em.rfc_emisor} ({em.total_facturas})
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
                <th>XML</th>
              </tr>
            </thead>
            <tbody>
              {facturas.map((f) => (
                <tr
                  key={f.id_factura_recibida}
                  className={`facturas-row${
                    (f.sat_estado ?? "").toLowerCase().includes("cancel")
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
                  <td>{f.nombre_emisor ?? f.rfc_emisor}</td>
                  <td className="facturas-cell-muted">
                    {f.fecha_emision.slice(0, 10)}
                  </td>
                  <td className="facturas-cell-mono">${formatMonto(f.monto_total)}</td>
                  <td>
                    <EstadoSatBadge estado={f.sat_estado} />
                  </td>
                  <td>
                    {f.tiene_xml ? (
                      <a
                        className="recibidas-xml-link"
                        href={urlXmlRecibida(f.id_factura_recibida)}
                        onClick={(e) => e.stopPropagation()}
                      >
                        Descargar
                      </a>
                    ) : (
                      <span style={{ color: "#c4cad6", fontSize: 12 }}>—</span>
                    )}
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
    </div>
  );
}

function HistorialSincronizacion({ onVolver }: { onVolver: () => void }) {
  const [corridas, setCorridas] = useState<CorridaPortal[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    listarHistorialSincronizacion(20)
      .then(setCorridas)
      .catch((err) => {
        setError(
          err instanceof ApiError
            ? err.message
            : "No se pudo cargar el historial."
        );
      })
      .finally(() => setIsLoading(false));
  }, []);

  function badgeEstado(estado: string) {
    if (estado === "EXITOSA")
      return { background: "#e5f0e8", color: "#2e7d5b" };
    if (estado === "FALLIDA")
      return { background: "#fbe7e7", color: "#a33b3b" };
    return { background: "#fdf1de", color: "#8a6d1f" };
  }

  return (
    <div className="facturas-panel">
      <div className="factura-detalle-header">
        <span className="factura-volver" onClick={onVolver}>
          ← Volver
        </span>
        <span className="factura-detalle-divider">|</span>
        <h2 className="factura-detalle-title">Historial de sincronizaciones</h2>
      </div>

      {isLoading && <p className="facturas-status">Cargando historial…</p>}
      {error && <p className="facturas-status facturas-status-error">{error}</p>}

      {!isLoading && !error && (
        <>
          <table className="facturas-table">
            <thead>
              <tr>
                <th>Inicio</th>
                <th>Tipo</th>
                <th>Estado</th>
                <th>Rango consultado</th>
                <th>Nuevas / actualizadas</th>
              </tr>
            </thead>
            <tbody>
              {corridas.map((c) => (
                <tr key={c.id} className="facturas-row">
                  <td className="facturas-cell-muted">
                    {c.inicio.replace("T", " ").slice(0, 16)}
                  </td>
                  <td>{c.motivo}</td>
                  <td>
                    <span className="factura-badge" style={badgeEstado(c.estado)}>
                      {c.estado}
                    </span>
                    {c.estado === "FALLIDA" && c.error && (
                      <p
                        style={{
                          margin: "6px 0 0",
                          fontSize: 12,
                          color: "#a33b3b",
                        }}
                      >
                        {c.error}
                      </p>
                    )}
                  </td>
                  <td className="facturas-cell-muted">
                    {c.fecha_desde} – {c.fecha_hasta}
                  </td>
                  <td className="facturas-cell-mono">
                    {c.nuevas ?? 0} / {c.actualizadas ?? 0}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>

          {corridas.length === 0 && (
            <p className="facturas-status">
              Todavía no hay sincronizaciones registradas.
            </p>
          )}
        </>
      )}
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
          Factura de {factura.nombre_emisor ?? factura.rfc_emisor}
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
            {factura.nombre_emisor ?? "—"}
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
            {factura.efecto_comprobante ?? "—"}
          </div>
        </div>
        <div className="factura-campo">
          <label className="factura-detalle-label">Origen</label>
          <div className="factura-campo-valor factura-campo-readonly">
            {factura.origen}
          </div>
        </div>
        {factura.fecha_vista_portal && (
          <div className="factura-campo">
            <label className="factura-detalle-label">Vista en el portal</label>
            <div className="factura-campo-valor factura-campo-readonly">
              {factura.fecha_vista_portal.replace("T", " ").slice(0, 16)}
            </div>
          </div>
        )}
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

      <div className="factura-detalle-archivo">
        <label className="factura-detalle-label">Archivo</label>
        {factura.tiene_xml ? (
          <a className="factura-file-card" href={urlXmlRecibida(factura.id_factura_recibida)}>
            <span className="factura-file-icon">XML</span>
            <div>
              <p className="factura-file-name">{factura.folio_fiscal.slice(0, 8)}….xml</p>
              <p className="factura-file-action">Descargar archivo</p>
            </div>
          </a>
        ) : (
          <p
            className="facturas-status"
            style={{ padding: 0, textAlign: "left" }}
          >
            Esta factura no tiene XML. El SAT no entrega el archivo de los
            comprobantes cancelados.
          </p>
        )}
      </div>
    </div>
  );
}