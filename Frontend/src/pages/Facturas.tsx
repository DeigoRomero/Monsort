import { Fragment, useEffect, useState, type ReactNode } from "react";
import {
  listarFacturas,
  listarEstados,
  obtenerFactura,
  listarOcsCandidatas,
  vincularOc,
  actualizarFactura,
  cancelarFactura,
  verificarSat,
  abrirPdfFactura,
  descargarReporteGeneral,
  descargarReporteDetalle,
  type FacturaListado,
  type FacturaDetalle as FacturaDetalleType,
  type OrdenCompraCandidata,
  type FiltrosFacturas,
  type ResumenFacturas,
  type EstadoOpcion,
  type VerificacionSat,
} from "../api/facturas";
import { ApiError } from "../api/client";
import { CancelarModal } from "../Components/CancelarModal";
import { ArchivoLink } from "../Components/ArchivoLink";
import { BotonFiltros, Flecha, Plegable, VistaAnimada } from "../Components/Animaciones";
import { useSalida } from "../Components/movimiento";
import "./Facturas.css";

function estiloEstado(nombre: string) {
  const n = nombre.toLowerCase();
  if (n.includes("cancel")) return { bg: "#eef0f3", color: "#8a92a5" };
  if (n.includes("históric") || n.includes("historic"))
    return { bg: "#eef0f3", color: "#566078" };
  if (n.includes("verific") || n.includes("aprob") || n.includes("revisad"))
    return { bg: "#e5f0e8", color: "#2e7d5b" };
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

/**
 * Días que faltan para la fecha límite de pago (antes decía solo
 * "Vigente / Por vencer / Vencida"). El color sigue la misma regla:
 * verde con más de 7 días, ámbar de 0 a 7, rojo si ya venció.
 */
function DiasRestantes({
  alerta,
  dias,
  fechaLimite,
}: {
  alerta: FacturaListado["alerta_vencimiento"];
  dias: number | null;
  fechaLimite: string | null;
}) {
  if (alerta === "pagada") {
    return (
      <span className="factura-badge" style={{ background: "#eef0f3", color: "#566078" }}>
        Pagada
      </span>
    );
  }
  if (dias === null || dias === undefined || !alerta) {
    return <span style={{ color: "#c4cad6", fontSize: 12 }}>—</span>;
  }
  const estilos = {
    vigente: { bg: "#e5f0e8", color: "#2e7d5b" },
    por_vencer: { bg: "#fdf1de", color: "#8a6d1f" },
    vencida: { bg: "#fbe7e7", color: "#a33b3b" },
  } as const;
  const s = estilos[alerta as keyof typeof estilos] ?? estilos.vigente;
  let texto: string;
  if (dias > 1) texto = `Faltan ${dias} días`;
  else if (dias === 1) texto = "Vence mañana";
  else if (dias === 0) texto = "Vence hoy";
  else if (dias === -1) texto = "Venció ayer";
  else texto = `Vencida hace ${Math.abs(dias)} días`;
  return (
    <span
      className="factura-badge"
      style={{ background: s.bg, color: s.color }}
      title={fechaLimite ? `Fecha límite de pago: ${fechaLimite}` : undefined}
    >
      {texto}
    </span>
  );
}

function esHistorica(estado: string) {
  const n = estado.toLowerCase();
  return n.includes("históric") || n.includes("historic");
}

function formatMonto(valor?: number | string | null) {
  if (valor === null || valor === undefined || valor === "") return "—";
  const num = typeof valor === "string" ? Number(valor) : valor;
  if (Number.isNaN(num)) return "—";
  return num.toLocaleString("es-MX", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
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
  // Fila con el resumen desplegado (acordeón). Una a la vez.
  const [expandido, setExpandido] = useState<number | null>(null);
  const [filtrosAbiertos, setFiltrosAbiertos] = useState(false);
  const { saliendo, salir } = useSalida();

  const [q, setQ] = useState("");
  const [cliente, setCliente] = useState("");
  const [numeroOc, setNumeroOc] = useState("");
  const [estadoFiltro, setEstadoFiltro] = useState("");
  const [origen, setOrigen] = useState("");
  const [conCp, setConCp] = useState<"todos" | "true" | "false">("todos");
  const [incluirCanceladas, setIncluirCanceladas] = useState(false);
  const [fechaDesde, setFechaDesde] = useState("");
  const [fechaHasta, setFechaHasta] = useState("");

  const [descargandoReporte, setDescargandoReporte] = useState(false);

  useEffect(() => {
    listarEstados().then(setEstados).catch(() => {});
  }, []);

  useEffect(() => {
    const id = setTimeout(() => cargar(1), 400);
    return () => clearTimeout(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [
    q,
    cliente,
    numeroOc,
    estadoFiltro,
    origen,
    conCp,
    incluirCanceladas,
    fechaDesde,
    fechaHasta,
  ]);

  function filtrosActuales(paginaOverride?: number): FiltrosFacturas {
    return {
      q: q || undefined,
      cliente: cliente || undefined,
      numero_oc: numeroOc || undefined,
      estado: estadoFiltro || undefined,
      origen: origen || undefined,
      con_cp: conCp === "todos" ? undefined : conCp === "true",
      incluir_canceladas: incluirCanceladas,
      incluir_historico: true,
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
        setError(
          err instanceof ApiError ? err.message : "No se pudieron cargar las facturas."
        );
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

  // Filtros aplicados además de la búsqueda: se muestran en el botón aunque
  // el panel esté cerrado, para que nadie se confunda con una tabla filtrada.
  const filtrosActivos = [
    cliente, numeroOc, estadoFiltro, origen, fechaDesde, fechaHasta,
  ].filter(Boolean).length + (conCp !== "todos" ? 1 : 0) + (incluirCanceladas ? 1 : 0);

  function abrirDetalle(id: number) {
    salir(() => setIdSeleccionado(id));
  }

  if (idSeleccionado !== null) {
    return (
      <VistaAnimada key={`detalle-${idSeleccionado}`} tipo="detalle" saliendo={saliendo}>
        <FacturaDetalleView
          idFactura={idSeleccionado}
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
    <div className="facturas-panel">
      <div className="facturas-header">
        <div>
          <p className="facturas-eyebrow">Facturas emitidas</p>
          <h2 className="facturas-title">Bandeja de verificación</h2>        </div>
        <button
          className="factura-btn-primary"
          onClick={handleReporteGeneral}
          disabled={descargandoReporte}
        >
          {descargandoReporte ? "Generando…" : "Reporte general"}
        </button>
      </div>

      <div className="facturas-filtros">
        <div className="filtros-barra">
          <input
            className="field-input facturas-buscador"
            placeholder="Buscar por cliente, UUID, folio interno u OC…"
            value={q}
            onChange={(e) => setQ(e.target.value)}
          />
          <BotonFiltros
            abierto={filtrosAbiertos}
            activos={filtrosActivos}
            onClick={() => setFiltrosAbiertos((v) => !v)}
            controla="filtros-emitidas"
          />
        </div>

        <Plegable abierto={filtrosAbiertos} id="filtros-emitidas">
        <div className="filtros-plegables">
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
            value={origen}
            onChange={(e) => setOrigen(e.target.value)}
          >
            <option value="">Todos los orígenes</option>
            <option value="gmail">Gmail</option>
            <option value="excel">Excel (histórico)</option>
            <option value="sat">SAT</option>
            <option value="manual">Manual</option>
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
              checked={incluirCanceladas}
              onChange={(e) => setIncluirCanceladas(e.target.checked)}
            />
            Incluir canceladas
          </label>
        </div>
        </div>
        </Plegable>
      </div>

      {resumen && (
        <>
          <div className="facturas-resumen">
            <div>
              <p className="facturas-resumen-label">Facturas</p>
              <p className="facturas-resumen-valor">{resumen.total_facturas}</p>
            </div>
            <div title={`Suma del importe (sin IVA). Con IVA: $${formatMonto(resumen.total_mxn)}`}>
              <p className="facturas-resumen-label">Total facturado (importe) MXN</p>
              <p className="facturas-resumen-valor">${formatMonto(resumen.importe_mxn)}</p>
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

          {resumen.total_historico > 0 && (
            <p className="facturas-nota-historico">
              El total incluye {resumen.total_historico} facturas del histórico migrado
              (${formatMonto(resumen.importe_mxn_historico)} de importe).
            </p>
          )}
        </>
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
                <th>Importe</th>
                <th>Total</th>
                <th>Estado</th>
                <th>Días para pago</th>
              </tr>
            </thead>
            <tbody>
              {facturas.map((f) => {
                const historica = esHistorica(f.estado);
                const cancelada = f.estado.toLowerCase().includes("cancel");
                const abierta = expandido === f.id_factura;
                return (
                  <Fragment key={f.id_factura}>
                  <tr
                    className={`facturas-row${cancelada ? " facturas-row-cancelada" : ""}${
                      abierta ? " facturas-row-expandida" : ""
                    }`}
                    onClick={() => setExpandido(abierta ? null : f.id_factura)}
                    onDoubleClick={() => abrirDetalle(f.id_factura)}
                    aria-expanded={abierta}
                  >
                    <td className="facturas-cell-strong">
                      <span className="fila-flecha">
                        <Flecha abierta={abierta} />
                      </span>
                      {f.folio_fiscal}
                      {historica && (
                        <span className="facturas-tag-historico">histórico</span>
                      )}
                    </td>
                    <td>{f.cliente}</td>
                    <td className="facturas-cell-muted">{f.fecha}</td>
                    <td className="facturas-cell-mono">
                      ${formatMonto(f.subtotal)}{" "}
                      {f.moneda && f.moneda !== "MXN" ? f.moneda : ""}
                    </td>
                    <td className="facturas-cell-mono facturas-cell-muted">
                      ${formatMonto(f.total)}{" "}
                      {f.moneda && f.moneda !== "MXN" ? f.moneda : ""}
                    </td>
                    <td>
                      <EstadoBadge nombre={f.estado} />
                    </td>
                    <td>
                      <DiasRestantes
                        alerta={f.alerta_vencimiento}
                        dias={f.dias_restantes}
                        fechaLimite={f.fecha_limite_pago}
                      />
                    </td>
                  </tr>
                  <tr className="fila-acordeon">
                    <td colSpan={7}>
                      <Plegable abierto={abierta}>
                        <ResumenFila factura={f} onVerDetalle={() => abrirDetalle(f.id_factura)} />
                      </Plegable>
                    </td>
                  </tr>
                  </Fragment>
                );
              })}
            </tbody>
          </table>

          {facturas.length === 0 && (
            <p className="facturas-status">
              No hay facturas que coincidan con los filtros.
            </p>
          )}

          <div className="facturas-paginacion">
            <button
              className="factura-btn-secondary"
              disabled={pagina <= 1}
              onClick={() => {
                setExpandido(null);
                cargar(pagina - 1);
              }}
            >
              ← Anterior
            </button>
            <span className="facturas-paginacion-info">
              Página {pagina} de {totalPaginas}
            </span>
            <button
              className="factura-btn-secondary"
              disabled={pagina >= totalPaginas}
              onClick={() => {
                setExpandido(null);
                cargar(pagina + 1);
              }}
            >
              Siguiente →
            </button>
          </div>
        </>
      )}
    </div>
    </VistaAnimada>
  );
}

/**
 * Resumen que se despliega bajo la fila (acordeón): lo que más se consulta
 * sin tener que abrir el detalle completo.
 */
function ResumenFila({
  factura: f,
  onVerDetalle,
}: {
  factura: FacturaListado;
  onVerDetalle: () => void;
}) {
  const moneda = f.moneda && f.moneda !== "MXN" ? ` ${f.moneda}` : "";
  const dato = (etiqueta: string, valor: ReactNode, mono = false) => (
    <div className="fila-acordeon-dato">
      <span className="fila-acordeon-etiqueta">{etiqueta}</span>
      <span className={`fila-acordeon-valor${mono ? " fila-acordeon-mono" : ""}`}>
        {valor ?? "—"}
      </span>
    </div>
  );
  const chip = (texto: string, hay: boolean) => (
    <span className={`chip-archivo${hay ? "" : " chip-archivo-falta"}`}>{texto}</span>
  );
  return (
    <div className="fila-acordeon-contenido">
      {dato("Folio interno", f.folio_interno)}
      {dato("OC", f.numero_oc, true)}
      {dato("RFC", f.rfc, true)}
      {dato("Importe", `$${formatMonto(f.subtotal)}${moneda}`, true)}
      {dato("IVA", `$${formatMonto(f.iva)}${moneda}`, true)}
      {dato("Total", `$${formatMonto(f.total)}${moneda}`, true)}
      {moneda && dato("Tipo de cambio", f.tipo_cambio, true)}
      {dato("Fecha límite de pago", f.fecha_limite_pago)}
      {dato("Liquidada", f.fecha_liquidacion)}
      <div className="fila-acordeon-dato">
        <span className="fila-acordeon-etiqueta">Documentos</span>
        <span className="chips-archivos">
          {chip("PDF", f.tiene_pdf)}
          {chip("XML", f.tiene_xml)}
          {chip("OC", f.tiene_oc)}
          {chip("CP", f.tiene_cp)}
        </span>
      </div>
      <div className="fila-acordeon-acciones">
        <button
          className="factura-btn-primary"
          onClick={(e) => {
            e.stopPropagation();
            onVerDetalle();
          }}
        >
          Ver detalle completo →
        </button>
      </div>
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
  const [verificandoSat, setVerificandoSat] = useState(false);
  const [resultadoSat, setResultadoSat] = useState<VerificacionSat | null>(null);
  const [errorSat, setErrorSat] = useState<string | null>(null);

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
        setError(
          err instanceof ApiError ? err.message : "No se pudo cargar la factura."
        );
      })
      .finally(() => setIsLoading(false));
  }

  function handleVerCandidatas() {
    setMostrarCandidatas(true);
    listarOcsCandidatas(idFactura)
      .then(setCandidatas)
      .catch(() =>
        setError("No se pudieron cargar las órdenes de compra candidatas.")
      );
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
      setError(
        err instanceof ApiError
          ? err.message
          : "No se pudo vincular la orden de compra."
      );
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
      setError(
        err instanceof ApiError ? err.message : "No se pudo guardar la factura."
      );
    } finally {
      setIsSaving(false);
    }
  }

  async function handleVerificarSat() {
    if (!factura) return;
    setVerificandoSat(true);
    setErrorSat(null);
    setResultadoSat(null);
    try {
      const resultado = await verificarSat(factura.id_factura);
      setResultadoSat(resultado);
      if (resultado.cambio_aplicado) {
        cargarFactura();
      }
    } catch (err) {
      if (err instanceof DOMException && err.name === "AbortError") {
        setErrorSat(
          "La verificación tardó demasiado. El SAT puede haber completado el cambio de todas formas — revisa la factura en unos momentos."
        );
      } else {
        setErrorSat(
          err instanceof ApiError ? err.message : "No se pudo verificar ante el SAT."
        );
      }
    } finally {
      setVerificandoSat(false);
    }
  }

  async function handleCancelar(motivo: string) {
    try {
      await cancelarFactura(idFactura, motivo);
      setMostrarCancelar(false);
      onVolver();
    } catch (err) {
      setError(
        err instanceof ApiError ? err.message : "No se pudo cancelar la factura."
      );
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

  const historica = esHistorica(factura.estado);

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

      {historica && (
        <p className="facturas-nota-historico">
          Esta factura viene del histórico migrado desde Excel. No tiene archivos
          adjuntos ni fecha de validación, y no puede marcarse como revisada.
        </p>
      )}

      <div className="factura-detalle-grid">
        <div className="factura-campo">
          <label className="factura-detalle-label">Cliente</label>
          <div className="factura-campo-valor factura-campo-readonly">
            {factura.cliente}
          </div>
        </div>
        <div className="factura-campo">
          <label className="factura-detalle-label">RFC</label>
          <div className="factura-campo-valor factura-campo-readonly">
            {factura.rfc}
          </div>
        </div>
        <div className="factura-campo">
          <label className="factura-detalle-label">Fecha</label>
          <div className="factura-campo-valor factura-campo-readonly">
            {factura.fecha}
          </div>
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
            ${formatMonto(factura.total)}{" "}
            {factura.moneda && factura.moneda !== "MXN" ? factura.moneda : ""}
          </div>
        </div>

        <Campo label="Número de OC" value={numeroOc} onChange={setNumeroOc} />
        <Campo
          label="Folio interno"
          value={folioInterno}
          onChange={setFolioInterno}
        />
        <div className="factura-campo">
          <label className="factura-detalle-label">Fecha de validación</label>
          <input
            className="factura-campo-input"
            type="date"
            value={fechaValidacion}
            onChange={(e) => setFechaValidacion(e.target.value)}
            disabled={historica}
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
            <p
              style={{
                margin: "2px 0 0",
                fontSize: 12.5,
                color: "var(--text-muted)",
              }}
            >
              Recibida el {factura.orden_compra.fecha_recepcion.slice(0, 10)}
            </p>
          </div>


        ) : (
          <p
            className="facturas-status"
            style={{ padding: 0, textAlign: "left", margin: "0 0 10px" }}
          >
            Esta factura no tiene una orden de compra vinculada todavía.
          </p>
        )}

        {!mostrarCandidatas ? (
          <button className="factura-btn-secondary" onClick={handleVerCandidatas}>
            {factura.orden_compra
              ? "Cambiar vínculo con OC"
              : "Vincular orden de compra"}
          </button>
        ) : (
          <div className="factura-candidatas">
            {candidatas.length === 0 ? (
              <p
                className="facturas-status"
                style={{ padding: 0, textAlign: "left" }}
              >
                No hay órdenes de compra candidatas para esta factura.
              </p>
            ) : (
              candidatas.map((c) => (
                <div key={c.id} className="factura-candidata-card">
                  <div>
                    <p
                      style={{
                        margin: "0 0 3px",
                        fontWeight: 600,
                        fontSize: 13.5,
                        color: "var(--text-ink)",
                      }}
                    >
                      {c.numero_oc}
                    </p>
                    <p
                      style={{
                        margin: 0,
                        fontSize: 12,
                        color: "var(--text-muted)",
                      }}
                    >
                      Recibida {c.fecha_recepcion.slice(0, 10)} ·{" "}
                      {c.facturas_asociadas}{" "}
                      {c.facturas_asociadas === 1
                        ? "factura asociada"
                        : "facturas asociadas"}
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

            {factura.complementos.length > 0 && (
        <div className="factura-detalle-oc">
          <label className="factura-detalle-label">
            Complemento{factura.complementos.length > 1 ? "s" : ""} de pago vinculado
            {factura.complementos.length > 1 ? "s" : ""}
          </label>
          <div className="factura-candidatas">
            {factura.complementos.map((cp) => (
              <div key={cp.id} className="factura-candidata-card">
                <div>
                  <p style={{ margin: "0 0 3px", fontWeight: 600, fontSize: 13.5, color: "var(--text-ink)" }}>
                    {cp.folio ?? cp.uuid_cp.slice(0, 8) + "…"}
                    {cp.num_parcialidad && (
                      <span style={{ fontWeight: 400, color: "var(--text-muted)", marginLeft: 6 }}>
                        parcialidad {cp.num_parcialidad}
                      </span>
                    )}
                  </p>
                  <p style={{ margin: 0, fontSize: 12, color: "var(--text-muted)" }}>
                    {cp.fecha_pago ? cp.fecha_pago.slice(0, 10) : "sin fecha"} · pagado $
                    {cp.imp_pagado?.toLocaleString("es-MX", { minimumFractionDigits: 2 }) ?? "—"}
                    {cp.imp_saldo_insoluto !== null && (
                      <> · saldo ${cp.imp_saldo_insoluto.toLocaleString("es-MX", { minimumFractionDigits: 2 })}</>
                    )}
                  </p>
                </div>
                {cp.liquida && (
                  <span className="factura-badge" style={{ background: "#e5f0e8", color: "#2e7d5b" }}>
                    Liquidada
                  </span>
                )}
              </div>
            ))}
          </div>
        </div>
      )}

      <div className="factura-detalle-archivo">
        <label className="factura-detalle-label">Archivo</label>
        {factura.tiene_pdf ? (
          <ArchivoLink
            className="factura-file-card"
            accion={() => abrirPdfFactura(factura.id_factura)}
          >
            <span className="factura-file-icon">PDF</span>
            <div>
              <p className="factura-file-name">Ver factura</p>
              <p className="factura-file-action">Abrir documento</p>
            </div>
          </ArchivoLink>
        ) : (
          <p
            className="facturas-status"
            style={{ padding: 0, textAlign: "left" }}
          >
            Esta factura no tiene PDF adjunto.
          </p>
        )}
      </div>

      <div className="factura-detalle-sat">
        <label className="factura-detalle-label">Estatus ante el SAT</label>
        <button
          className="factura-btn-secondary"
          onClick={handleVerificarSat}
          disabled={verificandoSat}
        >
          {verificandoSat
            ? "Consultando al SAT… (puede tardar hasta 30s)"
            : "Verificar ante SAT"}
        </button>

        {errorSat && (
          <p
            className="facturas-status facturas-status-error"
            style={{ padding: "10px 0 0" }}
          >
            {errorSat}
          </p>
        )}

        {resultadoSat && (
          <div className="factura-sat-resultado">
            <div className="factura-sat-fila">
              <span className="factura-sat-etiqueta">Estado SAT</span>
              <span className="factura-sat-valor">{resultadoSat.sat_estado}</span>
            </div>
            <div className="factura-sat-fila">
              <span className="factura-sat-etiqueta">¿Es cancelable?</span>
              <span className="factura-sat-valor">
                {resultadoSat.sat_es_cancelable}
              </span>
            </div>
            <div className="factura-sat-fila">
              <span className="factura-sat-etiqueta">Estatus de cancelación</span>
              <span className="factura-sat-valor">
                {resultadoSat.sat_estatus_cancelacion || "—"}
              </span>
            </div>
            {resultadoSat.cambio_aplicado && (
              <p className="factura-sat-nota">
                ⚠️ El estado local se actualizó automáticamente según el SAT.
              </p>
            )}
          </div>
        )}
      </div>

      {guardadoOk && !error && (
        <p
          className="facturas-status"
          style={{ color: "#2e7d5b", textAlign: "left", padding: "0 24px" }}
        >
          Cambios guardados ✓
        </p>
      )}
      {error && <p className="facturas-status facturas-status-error">{error}</p>}

      <div className="factura-detalle-actions">
        <button className="cancelar-btn" onClick={() => setMostrarCancelar(true)}>
          Cancelar factura
        </button>
        <button
          className="factura-btn-secondary"
          onClick={handleReporteDetalle}
          disabled={descargando}
        >
          {descargando ? "Generando…" : "Reporte detalle"}
        </button>
        <button
          className="factura-btn-primary"
          onClick={handleGuardar}
          disabled={isSaving}
        >
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