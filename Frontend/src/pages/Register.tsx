import { useState, type FormEvent } from "react";
import { registro } from "../api/auth";
import { ApiError } from "../api/client";
import "./Register.css";

interface RegisterProps {
  onCancelar: () => void;
}

export function Register({ onCancelar }: RegisterProps) {
  const [nombre, setNombre] = useState("");
  const [correo, setCorreo] = useState("");
  const [rol, setRol] = useState("");
  const [password, setPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [success, setSuccess] = useState(false);

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);

    if (password !== confirmPassword) {
      setError("Las contraseñas no coinciden.");
      return;
    }

    setIsLoading(true);
    try {
      await registro({ nombre, correo, password, rol });
      setSuccess(true);
    } catch (err) {
      if (err instanceof ApiError) {
        setError(err.message);
      } else {
        setError("No se pudo conectar con el servidor. Verifica tu conexión.");
      }
    } finally {
      setIsLoading(false);
    }
  }

  if (success) {
    return (
      <div className="facturas-panel">
        <div className="factura-detalle-header">
          <span className="factura-volver" onClick={onCancelar}>
            ← Volver
          </span>
        </div>
        <div style={{ padding: "40px 24px", textAlign: "center" }}>
          <h2 className="register-title">Usuario creado ✓</h2>
          <p className="register-subtitle">
            La cuenta de {nombre || "el empleado"} se registró correctamente.
          </p>
          <button
            className="factura-btn-primary"
            onClick={onCancelar}
            style={{ marginTop: 8 }}
          >
            Volver a facturas
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="facturas-panel">
      <div className="factura-detalle-header">
        <span className="factura-volver" onClick={onCancelar}>
          ← Volver
        </span>
        <span className="factura-detalle-divider">|</span>
        <h2 className="factura-detalle-title">Crear usuario</h2>
      </div>

      <form
        className="register-form"
        onSubmit={handleSubmit}
        noValidate
        style={{ padding: "24px", maxWidth: "380px" }}
      >
        <label className="field">
          <span className="field-label">Nombre completo</span>
          <input
            className="field-input"
            type="text"
            autoComplete="name"
            required
            value={nombre}
            onChange={(e) => setNombre(e.target.value)}
            placeholder="Juan Pérez"
          />
        </label>

        <label className="field">
          <span className="field-label">Correo electrónico</span>
          <input
            className="field-input"
            type="email"
            autoComplete="off"
            required
            value={correo}
            onChange={(e) => setCorreo(e.target.value)}
            placeholder="nombre@empresa.com"
          />
        </label>

        <label className="field">
          <span className="field-label">Puesto</span>
          <select
            className="field-input"
            required
            value={rol}
            onChange={(e) => setRol(e.target.value)}
          >
            <option value="" disabled>
              Selecciona un puesto
            </option>
            <option value="empleado">Empleado</option>
            <option value="administrador">Administrador</option>
          </select>
        </label>

        <label className="field">
          <span className="field-label">Contraseña temporal</span>
          <input
            className="field-input"
            type="password"
            autoComplete="new-password"
            required
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            placeholder="••••••••"
          />
        </label>

        <label className="field">
          <span className="field-label">Confirmar contraseña</span>
          <input
            className="field-input"
            type="password"
            autoComplete="new-password"
            required
            value={confirmPassword}
            onChange={(e) => setConfirmPassword(e.target.value)}
            placeholder="••••••••"
          />
        </label>

        {error && (
          <p className="register-error" role="alert">
            {error}
          </p>
        )}

        <div style={{ display: "flex", gap: 10, marginTop: 8 }}>
          <button
            type="button"
            className="factura-btn-secondary"
            onClick={onCancelar}
          >
            Cancelar
          </button>
          <button
            className="factura-btn-primary"
            type="submit"
            disabled={isLoading}
          >
            {isLoading ? "Creando…" : "Crear usuario"}
          </button>
        </div>
      </form>
    </div>
  );
}