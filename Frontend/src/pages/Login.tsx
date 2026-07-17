import { useState, type FormEvent } from "react";
import { useAuth } from "../context/AuthContext";
import "./Login.css";

export function Login() {
  const { signIn, isLoading, error, clearError } = useAuth();
  const [correo, setCorreo] = useState("");
  const [password, setPassword] = useState("");

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    clearError();
    await signIn(correo, password);
  }

  return (
    <div className="login-screen">
      <aside className="login-side-panel" aria-hidden="true">
        <svg className="login-wavy-grid" xmlns="http://www.w3.org/2000/svg">
          <defs>
            <pattern
              id="wavyGrid"
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
          <rect width="100%" height="100%" fill="url(#wavyGrid)" />
        </svg>

        <span
          className="login-glow-dot"
          style={{ top: "18%", left: "18%" }}
        />
        <span
          className="login-glow-dot"
          style={{ top: "38%", left: "58%", animationDelay: "1.2s" }}
        />
        <span
          className="login-glow-dot"
          style={{ top: "54%", left: "30%", animationDelay: "2.1s" }}
        />
        <span
          className="login-glow-dot"
          style={{ top: "28%", left: "76%", animationDelay: "0.6s" }}
        />
        <span
          className="login-glow-dot"
          style={{ top: "68%", left: "48%", animationDelay: "1.8s" }}
        />
        <span
          className="login-glow-dot"
          style={{ top: "14%", left: "48%", animationDelay: "2.7s" }}
        />

        <div className="login-side-top">
          <div className="login-logo-mark" />
          <span className="login-logo-text">MONSORT</span>
        </div>

        <div className="login-side-middle">
          <p className="login-side-eyebrow">Verificación de facturas</p>
          <h2 className="login-side-headline">
            Cada folio, cada monto, cada cuenta — verificado.
          </h2>
        </div>

        <p className="login-side-folio">
          FOLIO-ACCESO / {new Date().getFullYear()}
        </p>
      </aside>

      <main className="login-form-panel">
        <div className="login-form-wrap">
          <h1 className="login-title">Iniciar sesión</h1>
          <p className="login-subtitle">Accede a tu cuenta para continuar.</p>

          <form className="login-form" onSubmit={handleSubmit} noValidate>
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
              <span className="field-label">Contraseña</span>
              <input
                className="field-input"
                type="password"
                autoComplete="current-password"
                required
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                placeholder="••••••••"
              />
            </label>

            {error && (
              <p className="login-error" role="alert">
                {error}
              </p>
            )}

            <button
              className="login-submit"
              type="submit"
              disabled={isLoading}
            >
              {isLoading ? "Verificando…" : "Iniciar sesión →"}
            </button>
          </form>
        </div>
      </main>
    </div>
  );
}