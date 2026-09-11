import { useState } from "react";
import { useAuth } from "../context/AuthContext";
import { Facturas } from "./Facturas";
import { FacturasRecibidas } from "./FacturasRecibidas";
import { OrdenesCompra } from "./OrdenesCompra";
import { Clientes } from "./Clientes";
import { Register } from "./Register";
import "./Dashboard.css";

type Vista =
  | "facturas-emitidas"
  | "facturas-recibidas"
  | "ordenes"
  | "clientes"
  | "crear-usuario";

const ROLES_ADMIN = ["desarrollador", "administrador"];

export function Dashboard() {
  const { session, signOut } = useAuth();
  const [vista, setVista] = useState<Vista>("facturas-emitidas");

  const rol = session?.usuario.rol?.toLowerCase() ?? "";
  const puedeCrearUsuarios = ROLES_ADMIN.includes(rol);

  return (
    <div className="dashboard-layout">
      <aside className="dashboard-sidebar">
        <div className="dashboard-sidebar-logo">
          <div className="dashboard-logo-mark" />
          <span className="dashboard-logo-text">MONSORT</span>
        </div>

        <nav className="dashboard-nav">
          <button
            className={`dashboard-nav-item${vista === "facturas-emitidas" ? " active" : ""}`}
            onClick={() => setVista("facturas-emitidas")}
          >
            Facturas emitidas
          </button>
          <button
            className={`dashboard-nav-item${vista === "facturas-recibidas" ? " active" : ""}`}
            onClick={() => setVista("facturas-recibidas")}
          >
            Facturas recibidas
          </button>
          <button
            className={`dashboard-nav-item${vista === "ordenes" ? " active" : ""}`}
            onClick={() => setVista("ordenes")}
          >
            Órdenes de compra
          </button>
          <button
            className={`dashboard-nav-item${vista === "clientes" ? " active" : ""}`}
            onClick={() => setVista("clientes")}
          >
            Clientes
          </button>
        </nav>

        <div className="dashboard-sidebar-footer">
          {puedeCrearUsuarios && (
            <button
              className={`dashboard-nav-item${vista === "crear-usuario" ? " active" : ""}`}
              onClick={() => setVista("crear-usuario")}
            >
              + Crear usuario
            </button>
          )}
          <button
            className="dashboard-nav-item dashboard-signout-item"
            onClick={signOut}
          >
            Cerrar sesión
          </button>
        </div>
      </aside>

      <main className="dashboard-main">
        {vista === "facturas-emitidas" && <Facturas />}
        {vista === "facturas-recibidas" && <FacturasRecibidas />}
        {vista === "ordenes" && <OrdenesCompra />}
        {vista === "clientes" && <Clientes />}
        {vista === "crear-usuario" && (
          <Register onCancelar={() => setVista("facturas-emitidas")} />
        )}
      </main>
    </div>
  );
}