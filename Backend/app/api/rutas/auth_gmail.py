import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import HTMLResponse, RedirectResponse
from google_auth_oauthlib.flow import Flow
from sqlalchemy.orm import Session

from app.BaseDeDatos import get_db
from app.core.config import settings
from app.modelos.configuracion import Configuracion_sistema

logger = logging.getLogger(__name__)
router = APIRouter()

SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]


def _construir_flujo() -> Flow:
    """
    Se arma desde las variables de entorno, no desde credentials.json:
    en el VPS ese archivo no tiene por qué existir.
    """
    configuracion = {
        "web": {
            "client_id": settings.GMAIL_CLIENT_ID,
            "client_secret": settings.GMAIL_CLIENT_SECRET,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": [settings.GMAIL_REDIRECT_URI],
        }
    }
    flujo = Flow.from_client_config(configuracion, scopes=SCOPES)
    flujo.redirect_uri = settings.GMAIL_REDIRECT_URI
    return flujo


def _guardar(db: Session, clave: str, valor: str) -> None:
    fila = db.query(Configuracion_sistema).filter(
        Configuracion_sistema.clave == clave
    ).first()
    if fila:
        fila.valor = valor
    else:
        db.add(Configuracion_sistema(clave=clave, valor=valor))


@router.get("/gmail/iniciar")
def iniciar_autorizacion():
    """
    Redirige a Google. Este es el link que se le manda al cliente.

    prompt='consent' fuerza que Google devuelva refresh_token. Sin él,
    una cuenta ya autorizada devuelve solo access_token y el flujo
    parece exitoso pero no sirve de nada.
    """
    flujo = _construir_flujo()
    url, _ = flujo.authorization_url(
        access_type="offline",
        prompt="consent",
        include_granted_scopes="true",
    )
    return RedirectResponse(url)


@router.get("/gmail/callback", response_class=HTMLResponse)
def recibir_callback(
    code: str = Query(None),
    error: str = Query(None),
    db: Session = Depends(get_db),
):
    """Recibe el código de Google y guarda el refresh token."""
    if error:
        logger.warning("Autorización de Gmail rechazada: %s", error)
        return HTMLResponse(
            "<h2>Autorización cancelada</h2>"
            "<p>No se otorgaron los permisos. Puedes cerrar esta ventana.</p>",
            status_code=400,
        )

    if not code:
        raise HTTPException(status_code=400, detail="Falta el código")

    flujo = _construir_flujo()
    try:
        flujo.fetch_token(code=code)
    except Exception as e:
        logger.exception("Fallo al canjear el código de Gmail")
        raise HTTPException(status_code=400, detail=f"Código inválido: {e}")

    refresh_token = flujo.credentials.refresh_token
    if not refresh_token:
        return HTMLResponse(
            "<h2>No se recibió el token</h2>"
            "<p>Revoca el acceso en myaccount.google.com/permissions "
            "e intenta de nuevo.</p>",
            status_code=400,
        )

    _guardar(db, "gmail_refresh_token", refresh_token)

    # El historyId anterior es de la cuenta vieja y no significa nada
    # para la cuenta nueva. Se borra para que el sistema resiembre desde
    # el momento actual y no intente procesar años de correo histórico.
    viejo = db.query(Configuracion_sistema).filter(
        Configuracion_sistema.clave == "gmail_history_id"
    ).first()
    if viejo:
        db.delete(viejo)

    db.commit()
    logger.info("Refresh token de Gmail actualizado. historyId reiniciado.")

    return HTMLResponse(
        "<h2>Listo</h2>"
        "<p>La cuenta quedó conectada correctamente. "
        "Ya puedes cerrar esta ventana.</p>"
    )