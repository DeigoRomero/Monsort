import { useState, type FormEvent } from "react";
import { registro } from "../api/auth";
import { ApiError } from "../api/client";
import "./Register.css";

interface RegisterProps {
  onLoginClick: () => void;
}

export function Register({ onLoginClick }: RegisterProps) {
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
      <div className="register-screen">
        <aside className="register-side-panel" aria-hidden="true">
          <div className="register-side-top">
            <div className="register-logo-mark" />
            <span className="register-logo-text">MONSORT</span>
          </div>
        </aside>
        <main className="register-form-panel">
          <div className="register-form-wrap">
            <h1 className="register-title">Cuenta creada ✓</h1>
            <p className="register-subtitle">
              Tu cuenta se registró correctamente. Ya puedes iniciar sesión.
            </p>
            <button className="register-submit" onClick={onLoginClick}>
              Ir a iniciar sesión →
            </button>
          </div>
        </main>
      </div>
    );
  }

  return (
    <div className="register-screen">
      <aside className="register-side-panel" aria-hidden="true">
        <svg className="register-wavy-grid" xmlns="http://www.w3.org/2000/svg">
          <defs>
            <pattern
              id="wavyGridRegister"
              width="64"
              height="64"
              patternUnits="userSpaceOnUse"
            >
              <animateTransform
                attributeName="patternTransform"
                type="translate"
                from="0 0"
                to="64 64"
                dur="14s"
                repeatCount="indefinite"
              />
              <path
                d="M0 32 Q16 14 32 32 T64 32"
                stroke="rgba(231,236,245,0.10)"
                fill="none"
                strokeWidth="1"
              />
              <path
                d="M32 0 Q14 16 32 32 T32 64"
                stroke="rgba(231,236,245,0.10)"
                fill="none"
                strokeWidth="1"
              />
            </pattern>
          </defs>
          <rect width="100%" height="100%" fill="url(#wavyGridRegister)" />
        </svg>

        <span className="register-glow-dot" style={{ top: "18%", left: "18%" }} />
        <span
          className="register-glow-dot"
          style={{ top: "38%", left: "58%", animationDelay: "1.2s" }}
        />
        <span
          className="register-glow-dot"
          style={{ top: "54%", left: "30%", animationDelay: "2.1s" }}
        />
        <span
          className="register-glow-dot"
          style={{ top: "28%", left: "76%", animationDelay: "0.6s" }}
        />

        <div className="register-side-top">
          <div className="register-logo-mark" />
          <span className="register-logo-text">MONSORT</span>
        </div>

        <div className="register-side-middle">
          <p className="register-side-eyebrow">Crear cuenta</p>
          <h2 className="register-side-headline">
            Únete al portal de verificación de facturas.
          </h2>
        </div>

        <p className="register-side-folio">
          FOLIO-REGISTRO / {new Date().getFullYear()}
        </p>
      </aside>

      <main className="register-form-panel">
        <div className="register-form-wrap">
          <h1 className="register-title">Crear cuenta</h1>
          <p className="register-subtitle">
            Regístrate para empezar a usar Monsort.
          </p>

          <form className="register-form" onSubmit={handleSubmit} noValidate>
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
                autoComplete="username"
                required
                value={correo}
                onChange={(e) => setCorreo(e.target.value)}
                placeholder="nombre@empresa.com"
              />
            </label>

            <label className="field">
              <span className="field-label">
                Puesto{" "}
                <span className="field-label-hint">
                  (ej. desarrollador, usuario, administrador)
                </span>
              </span>
              <input
                className="field-input"
                type="text"
                required
                value={rol}
                onChange={(e) => setRol(e.target.value)}
                placeholder="Desarrollador"
              />
            </label>

            <label className="field">
              <span className="field-label">Contraseña</span>
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

            <button className="register-submit" type="submit" disabled={isLoading}>
              {isLoading ? "Creando cuenta…" : "Crear cuenta →"}
            </button>
          </form>

          <p className="register-login-link">
            ¿Ya tienes cuenta?{" "}
            <span onClick={onLoginClick}>Inicia sesión</span>
          </p>
        </div>
      </main>
    </div>
  );
}