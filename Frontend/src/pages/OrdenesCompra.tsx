import { useEffect, useRef, useState } from "react";
import {
  listarOrdenes,
  actualizarOrden,
  abrirArchivoOC,
  type FiltrosOrdenCompra,
  type OrdenCompra,
  type OrdenCompraActualizar,
} from "../api/ordenes-compra";
import { listarClientes, type Cliente } from "../api/clientes";
import { ApiError } from "../api/client";
import { ArchivoLink } from "../Components/ArchivoLink";
import { BotonFiltros, Plegable, VistaAnimada } from "../Components/Animaciones";
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

function formatMonto(monto: string | null, moneda: string | null) {
  if (monto === null || monto === "") return "—";
  const num = Number(monto);
  if (Number.isNaN(num)) return "—";
  const texto = num.toLocaleString("es-MX", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  return `$${texto}${moneda && moneda !== "MXN" ? ` ${moneda}` : ""}`;
}

/** "$12,500.50" -> "12500.50". null si no es un monto válido. */
function limpiarMonto(texto: string): string | null {
  const limpio = texto.replace(/[$,\s]/g, "");
  return /^\d{1,12}(\.\d{1,2})?$/.test(limpio) ? limpio : null;
}

const MONTO_PERMITIDO = /^[\d$,.\s]*$/;

export function OrdenesCompra() {
  const [ordenes, setOrdenes] = useState<OrdenCompra[]>([]);
  const [clientes, setClientes] = useState<Cliente[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [seleccionada, setSeleccionada] = useState<OrdenCompra | null>(null);
  const [filtrosAbiertos, setFiltrosAbiertos] = useState(false);
  const { saliendo, salir } = useSalida();

  const [q, setQ] = useState("");
  const [idCliente, setIdCliente] = useState("");
  const [fechaDesde, setFechaDesde] = useState("");
  const [fechaHasta, setFechaHasta] = useState("");
  const [montoMin, setMontoMin] = useState("");
  const [montoMax, setMontoMax] = useState("");
  const [conFactura, setConFactura] = useState<"todos" | "true" | "false">("todos");

  // Al escribir rápido salen varias peticiones; solo cuenta la última.
  const ultimaPeticion = useRef(0);

  useEffect(() => {
    // Inactivos incluidos: una OC vieja puede ser de un cliente dado de baja.
    listarClientes(false)
      .then((lista) => setClientes([...lista].sort((a, b) => a.nombre.localeCompare(b.nombre))))
      .catch(() => {});
  }, []);

  const montoMinValido = montoMin === "" || limpiarMonto(montoMin) !== null;
  const montoMaxValido = montoMax === "" || limpiarMonto(montoMax) !== null;

  function filtrosActuales(): FiltrosOrdenCompra {
    return {
      q: q.trim() || undefined,
      id_cliente: idCliente ? Number(idCliente) : undefined,
      fecha_desde: fechaDesde || undefined,
      fecha_hasta: fechaHasta || undefined,
      monto_min: montoMin ? limpiarMonto(montoMin) ?? undefined : undefined,
      monto_max: montoMax ? limpiarMonto(montoMax) ?? undefined : undefined,
      con_factura: conFactura === "todos" ? undefined : conFactura === "true",
    };
  }

  function cargar() {
    const numero = ++ultimaPeticion.current;
    setIsLoading(true);
    setError(null);
    listarOrdenes(filtrosActuales())
      .then((lista) => {
        if (numero === ultimaPeticion.current) setOrdenes(lista);
      })
      .catch((err) => {
        if (numero !== ultimaPeticion.current) return;
        setError(err instanceof ApiError ? err.message : "No se pudieron cargar las órdenes.");
      })
      .finally(() => {
        if (numero === ultimaPeticion.current) setIsLoading(false);
      });
  }

  useEffect(() => {
    if (!montoMinValido || !montoMaxValido) return;
    const id = setTimeout(cargar, 400);
    return () => clearTimeout(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [q, idCliente, fechaDesde, fechaHasta, montoMin, montoMax, conFactura]);

  const filtrosActivos =
    [idCliente, fechaDesde, fechaHasta, montoMin, montoMax].filter(Boolean).length +
    (conFactura !== "todos" ? 1 : 0);

  function limpiarFiltros() {
    setIdCliente("");
    setFechaDesde("");
    setFechaHasta("");
    setMontoMin("");
    setMontoMax("");
    setConFactura("todos");
  }

  if (seleccionada) {
    return (
      <VistaAnimada key={`detalle-${seleccionada.id}`} tipo="detalle" saliendo={saliendo}>
      <DetalleOC
        oc={seleccionada}
        clientes={clientes}
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
  const hayFiltros = filtrosActivos > 0 || q.trim() !== "";

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

      <div className="facturas-filtros">
        <div className="filtros-barra">
          <input
            className="field-input facturas-buscador"
            placeholder="Buscar por número de OC, cliente, archivo o monto…"
            value={q}
            maxLength={100}
            onChange={(e) => setQ(e.target.value)}
          />
          <BotonFiltros
            abierto={filtrosAbiertos}
            activos={filtrosActivos}
            onClick={() => setFiltrosAbiertos((v) => !v)}
            controla="filtros-ordenes"
          />
        </div>

        <Plegable abierto={filtrosAbiertos} id="filtros-ordenes">
        <div className="filtros-plegables">
        <div className="facturas-filtros-grid">
          <select
            className="field-input"
            aria-label="Cliente"
            value={idCliente}
            onChange={(e) => setIdCliente(e.target.value)}
          >
            <option value="">Todos los clientes</option>
            {clientes.map((c) => (
              <option key={c.id} value={c.id}>
                {c.nombre}
              </option>
            ))}
          </select>
          <select
            className="field-input"
            aria-label="Factura"
            value={conFactura}
            onChange={(e) => setConFactura(e.target.value as typeof conFactura)}
          >
            <option value="todos">Con y sin factura</option>
            <option value="true">Solo con factura</option>
            <option value="false">Solo sin factura</option>
          </select>
        </div>

        <div className="facturas-rango-fechas">
          <div className="facturas-rango-campo">
            <label className="facturas-rango-label">Recibida desde</label>
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
          <div className="facturas-rango-campo">
            <label className="facturas-rango-label" htmlFor="oc-monto-min">Monto desde</label>
            <input
              id="oc-monto-min"
              className="field-input"
              placeholder="0.00"
              inputMode="decimal"
              aria-invalid={!montoMinValido}
              value={montoMin}
              onChange={(e) => MONTO_PERMITIDO.test(e.target.value) && setMontoMin(e.target.value)}
            />
          </div>
          <div className="facturas-rango-campo">
            <label className="facturas-rango-label" htmlFor="oc-monto-max">Monto hasta</label>
            <input
              id="oc-monto-max"
              className="field-input"
              placeholder="0.00"
              inputMode="decimal"
              aria-invalid={!montoMaxValido}
              value={montoMax}
              onChange={(e) => MONTO_PERMITIDO.test(e.target.value) && setMontoMax(e.target.value)}
            />
          </div>
          {filtrosActivos > 0 && (
            <button type="button" className="factura-btn-secondary" onClick={limpiarFiltros}>
              Limpiar filtros
            </button>
          )}
        </div>
        {(!montoMinValido || !montoMaxValido) && (
          <p className="facturas-status facturas-status-error" style={{ padding: "8px 0 0", margin: 0 }}>
            Escribe el monto como 12500 o 12,500.50
          </p>
        )}
        </div>
        </Plegable>
      </div>

      {isLoading && ordenes.length === 0 && <p className="facturas-status">Cargando órdenes…</p>}
      {error && (
        <p className="facturas-status facturas-status-error">{error}</p>
      )}

      {!error && ordenes.length > 0 && (
        <table className="facturas-table" style={{ opacity: isLoading ? 0.6 : 1 }}>
          <thead>
            <tr>
              <th>Número OC</th>
              <th>Cliente</th>
              <th>Fecha recepción</th>
              <th style={{ textAlign: "right" }}>Monto</th>
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
                title={o.nombre_archivo ?? undefined}
              >
                <td className="facturas-cell-strong">
                  {o.numero_oc ?? <span style={{ color: "#8a92a5" }}>Sin número</span>}
                  <ConfianzaBadge confianza={o.confianza_oc} numero={o.numero_oc} />
                </td>
                <td className="facturas-cell-muted">{o.cliente ?? "—"}</td>
                <td className="facturas-cell-muted">
                  {o.fecha_recepcion?.slice(0, 10) ?? "—"}
                </td>
                <td className="facturas-cell-mono" style={{ textAlign: "right", whiteSpace: "nowrap" }}>
                  {formatMonto(o.monto, o.moneda)}
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
          {hayFiltros
            ? "No hay órdenes que coincidan con la búsqueda."
            : "No hay órdenes de compra registradas."}
        </p>
      )}
    </div>
    </VistaAnimada>
  );
}

function DetalleOC({
  oc,
  clientes,
  onVolver,
  onGuardado,
}: {
  oc: OrdenCompra;
  clientes: Cliente[];
  onVolver: () => void;
  onGuardado: (actualizada: OrdenCompra) => void;
}) {
  const [numeroOc, setNumeroOc] = useState(oc.numero_oc ?? "");
  const [idCliente, setIdCliente] = useState(oc.id_cliente ? String(oc.id_cliente) : "");
  const [monto, setMonto] = useState(oc.monto ?? "");
  const [moneda, setMoneda] = useState(oc.moneda ?? "MXN");
  const [isSaving, setIsSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [guardadoOk, setGuardadoOk] = useState(false);

  async function handleGuardar() {
    setError(null);
    setGuardadoOk(false);

    // Solo se manda lo que cambió: un PATCH con el número intacto no debe
    // marcar la OC como "corregida a mano".
    const cambios: OrdenCompraActualizar = {};
    const numero = numeroOc.trim();
    if (numero !== (oc.numero_oc ?? "")) {
      if (!numero) {
        setError("El número de OC no puede ir vacío.");
        return;
      }
      cambios.numero_oc = numero;
    }
    const cliente = idCliente ? Number(idCliente) : null;
    if (cliente !== oc.id_cliente) cambios.id_cliente = cliente;

    const montoLimpio = monto.trim() === "" ? null : limpiarMonto(monto);
    if (monto.trim() !== "" && montoLimpio === null) {
      setError("El monto no es válido. Escríbelo como 12500 o 12,500.50.");
      return;
    }
    if (montoLimpio === null ? oc.monto !== null : Number(montoLimpio) !== Number(oc.monto)) {
      cambios.monto = montoLimpio;
    }
    if (montoLimpio !== null && (cambios.monto !== undefined || moneda !== (oc.moneda ?? "MXN"))) {
      cambios.moneda = moneda;
    }

    if (Object.keys(cambios).length === 0) {
      setGuardadoOk(true);
      return;
    }

    setIsSaving(true);
    try {
      const actualizada = await actualizarOrden(oc.id, cambios);
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
            {oc.fecha_recepcion?.slice(0, 10) ?? "—"}
          </div>
        </div>
        <div className="factura-campo">
          <label className="factura-detalle-label">Facturas asociadas</label>
          <div className="factura-campo-valor factura-campo-readonly factura-campo-mono">
            {oc.facturas_asociadas}
          </div>
        </div>
        <div className="factura-campo">
          <label className="factura-detalle-label" htmlFor="oc-numero">
            Número OC (editable)
          </label>
          <input
            id="oc-numero"
            className="factura-campo-input"
            value={numeroOc}
            maxLength={50}
            onChange={(e) => setNumeroOc(e.target.value)}
            placeholder="OC-0000"
          />
        </div>
        <div className="factura-campo">
          <label className="factura-detalle-label" htmlFor="oc-cliente">
            Cliente (editable)
          </label>
          <select
            id="oc-cliente"
            className="factura-campo-input"
            value={idCliente}
            onChange={(e) => setIdCliente(e.target.value)}
          >
            <option value="">Sin cliente</option>
            {clientes.map((c) => (
              <option key={c.id} value={c.id}>
                {c.nombre}
              </option>
            ))}
          </select>
        </div>
        <div className="factura-campo">
          <label className="factura-detalle-label" htmlFor="oc-monto">
            Monto antes de IVA (editable)
          </label>
          <div style={{ display: "flex", gap: 8 }}>
            <input
              id="oc-monto"
              className="factura-campo-input factura-campo-mono"
              style={{ flex: 1, minWidth: 0 }}
              inputMode="decimal"
              value={monto}
              onChange={(e) => MONTO_PERMITIDO.test(e.target.value) && setMonto(e.target.value)}
              placeholder="0.00"
            />
            <select
              className="factura-campo-input"
              style={{ width: 90 }}
              aria-label="Moneda"
              value={moneda}
              onChange={(e) => setMoneda(e.target.value)}
            >
              <option value="MXN">MXN</option>
              <option value="USD">USD</option>
            </select>
          </div>
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
