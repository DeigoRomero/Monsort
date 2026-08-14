import { useAuth } from "../context/AuthContext";
import { Facturas } from "./Facturas";
import "./Dashboard.css";

export function Dashboard() {
  const { session, signOut } = useAuth();

  return (
    <div className="dashboard-screen">
      <header className="dashboard-header">
        <p className="dashboard-eyebrow">MONSORT · Portal interno</p>
        <button className="dashboard-signout" onClick={signOut}>
          Cerrar sesión
        </button>
      </header>

      <main className="dashboard-body">
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
      </main>
    </div>
  );
}