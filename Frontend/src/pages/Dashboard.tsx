import { useState } from "react";
import { useAuth } from "../context/AuthContext";
import { Facturas } from "./Facturas";
import { Register } from "./Register";
import "./Dashboard.css";

const ROLES_ADMIN = ["desarrollador", "administrador"];

export function Dashboard() {
  const { session, signOut } = useAuth();
  const [vista, setVista] = useState<"facturas" | "crear-usuario">("facturas");

  const rol = session?.usuario.rol?.toLowerCase() ?? "";
  const puedeCrearUsuarios = ROLES_ADMIN.includes(rol);

  return (
    <div className="dashboard-screen">
      <header className="dashboard-header">
        <p className="dashboard-eyebrow">MONSORT · Portal interno</p>
        <div className="dashboard-header-actions">
          {puedeCrearUsuarios && (
            <button
              className="dashboard-crear-usuario"
              onClick={() => setVista("crear-usuario")}
            >
              + Crear usuario
            </button>
          )}
          <button className="dashboard-signout" onClick={signOut}>
            Cerrar sesión
          </button>
        </div>
      </header>

      <main className="dashboard-body">
        {vista === "facturas" ? (
          <>
            <p className="dashboard-folio">
              FOLIO-SESIÓN / {new Date().getFullYear()}
            </p>
            <h1 className="dashboard-title">
              Bienvenido, {session?.usuario.nombre}
            </h1>
            <p className="dashboard-subtitle">
              Sesión iniciada como <strong>{session?.usuario.correo}</strong>{" "}
              ({session?.usuario.rol})
            </p>

            <Facturas />
          </>
        ) : (
          <Register onCancelar={() => setVista("facturas")} />
        )}
      </main>
    </div>
  );
}