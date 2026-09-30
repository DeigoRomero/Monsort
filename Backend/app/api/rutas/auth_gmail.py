import logging

from fastapi import APIRouter, Depends, Query
from fastapi.responses import HTMLResponse
from google_auth_oauthlib.flow import Flow
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.BaseDeDatos import get_db
from app.core.config import settings
from app.core.dependencias import ROLES_ADMIN, requiere_roles
from app.core.seguridad import crear_estado_oauth, decode_token
from app.modelos.configuracion import Configuracion_sistema
from app.modelos.usuario import Usuarios

logger = logging.getLogger(__name__)
router = APIRouter()

SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]
VIGENCIA_ENLACE_HORAS = 24


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
    # PKCE requiere conservar el 'code_verifier' entre /iniciar y
    # /callback, pero cada peticion arma su propio Flow y el de
    # /iniciar muere al responder. Este es un cliente web
    # confidencial: el client_secret vive solo en el servidor y ya
    # cumple esa funcion, asi que PKCE no aporta aqui.
    flujo.autogenerate_code_verifier = False
    flujo.code_verifier = None
    return flujo


def _guardar(db: Session, clave: str, valor: str) -> None:
    fila = db.query(Configuracion_sistema).filter(
        Configuracion_sistema.clave == clave
    ).first()
    if fila:
        fila.valor = valor
    else:
        db.add(Configuracion_sistema(clave=clave, valor=valor))


class EnlaceGmail(BaseModel):
    url: str
    vigencia_horas: int


@router.post("/gmail/enlace", response_model=EnlaceGmail)
def generar_enlace_autorizacion(
    admin: Usuarios = Depends(requiere_roles(*ROLES_ADMIN)),
):
    """
    Devuelve el enlace de Google para conectar (o reconectar) el buzón.

    Solo administrador/desarrollador. El enlace lleva un 'state' firmado por
    el servidor y vigente 24 h; se le puede mandar al dueño de la cuenta de
    Gmail para que autorice desde su navegador.

    Antes era GET /auth/gmail/iniciar, público y sin 'state': cualquiera
    podía completar el flujo con OTRA cuenta de Gmail, el sistema guardaba
    ese token y empezaba a leer un buzón ajeno (y borraba el historyId).

    prompt='consent' fuerza que Google devuelva refresh_token.
    """
    flujo = _construir_flujo()
    url, _ = flujo.authorization_url(
        access_type="offline",
        prompt="consent",
        include_granted_scopes="true",
        state=crear_estado_oauth(admin.id_usuario, horas=VIGENCIA_ENLACE_HORAS),
    )
    logger.info("Enlace de autorización de Gmail generado por %s", admin.correo)
    return EnlaceGmail(url=url, vigencia_horas=VIGENCIA_ENLACE_HORAS)


def _pagina(titulo: str, mensaje: str, status_code: int = 200) -> HTMLResponse:
    # Texto fijo, sin interpolar nada que venga de la petición (XSS reflejado).
    return HTMLResponse(
        f"<!doctype html><meta charset='utf-8'><title>{titulo}</title>"
        f"<h2>{titulo}</h2><p>{mensaje}</p>",
        status_code=status_code,
        headers={"Cache-Control": "no-store"},
    )


@router.get("/gmail/callback", response_class=HTMLResponse)
def recibir_callback(
    code: str | None = Query(None, max_length=2048),
    state: str | None = Query(None, max_length=4096),
    error: str | None = Query(None, max_length=200),
    db: Session = Depends(get_db),
):
    """Recibe el código de Google y guarda el refresh token."""
    datos_estado = decode_token(state or "", tipo="oauth_gmail")
    if not datos_estado:
        logger.warning("Callback de Gmail con 'state' inválido o vencido")
        return _pagina("Enlace inválido o vencido",
                       "Pide a un administrador un enlace nuevo.", 400)

    admin = db.query(Usuarios).filter(
        Usuarios.id_usuario == int(datos_estado.get("sub", 0))
    ).first()
    if not admin or not admin.activo or (admin.rol or "").lower() not in ROLES_ADMIN:
        return _pagina("Enlace inválido", "El enlace ya no es válido.", 400)

    if error:
        logger.warning("Autorización de Gmail rechazada: %s", error)
        return _pagina("Autorización cancelada",
                       "No se otorgaron los permisos. Puedes cerrar esta ventana.", 400)

    if not code:
        return _pagina("Falta el código", "Vuelve a abrir el enlace de autorización.", 400)

    flujo = _construir_flujo()
    try:
        flujo.fetch_token(code=code)
    except Exception:
        # El detalle va al log, no a la página
        logger.exception("Fallo al canjear el código de Gmail")
        return _pagina("No se pudo completar la autorización",
                       "El código no es válido o ya se usó. Pide un enlace nuevo.", 400)

    refresh_token = flujo.credentials.refresh_token
    if not refresh_token:
        return _pagina(
            "No se recibió el token",
            "Revoca el acceso en myaccount.google.com/permissions e intenta de nuevo.", 400,
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
    logger.info("Refresh token de Gmail actualizado (enlace de %s). historyId reiniciado.",
                admin.correo)

    return _pagina("Listo", "La cuenta quedó conectada correctamente. "
                   "Ya puedes cerrar esta ventana.")
