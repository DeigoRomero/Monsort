import { useState } from "react";

export function CancelarModal({
  titulo,
  onConfirmar,
  onCerrar,
}: {
  titulo: string;
  onConfirmar: (motivo: string) => void;
  onCerrar: () => void;
}) {
  const [motivo, setMotivo] = useState("");

  return (
    <div className="cancelar-modal-overlay" onClick={onCerrar}>
      <div className="cancelar-modal" onClick={(e) => e.stopPropagation()}>
        <h3 className="cancelar-modal-title">{titulo}</h3>
        <p className="cancelar-modal-warning">
          Esta acción no se puede deshacer desde aquí.
        </p>
        <label className="field">
          <span className="field-label">Motivo (opcional)</span>
          <input
            className="field-input"
            value={motivo}
            onChange={(e) => setMotivo(e.target.value)}
            placeholder="Ej. Factura duplicada"
          />
        </label>
        <div className="cancelar-modal-actions">
          <button className="factura-btn-secondary" onClick={onCerrar}>
            Volver
          </button>
          <button
            className="cancelar-modal-confirm"
            onClick={() => onConfirmar(motivo)}
          >
            Sí, cancelar
          </button>
        </div>
      </div>
    </div>
  );
}