from app.core.config import settings
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from sqlalchemy.orm import Session
from app.modelos.configuracion import Configuracion_sistema
import base64
import xml.etree.ElementTree as ET
import logging 
logger = logging.getLogger(__name__)

def obtener_servicio_gmail(db: Session | None = None):
    refresh_token = settings.GMAIL_REFRESH_TOKEN

    if db is not None:
        fila = db.query(Configuracion_sistema).filter(
            Configuracion_sistema.clave == "gmail_refresh_token"
        ).first()
        if fila and fila.valor:
            refresh_token = fila.valor

    if not refresh_token:
        raise RuntimeError(
            "No hay refresh token de Gmail. Autoriza desde /auth/gmail/iniciar"
        )
    creds = Credentials(
    token=None,
    client_id=settings.GMAIL_CLIENT_ID, 
    client_secret=settings.GMAIL_CLIENT_SECRET,  
    refresh_token=settings.GMAIL_REFRESH_TOKEN,
    token_uri="https://oauth2.googleapis.com/token"
    )
    servicio = build('gmail','v1', credentials=creds)
    return servicio

def obtener_mensajes_nuevos(db: Session):
    servicio = obtener_servicio_gmail()
    IdGuardado = db.query(Configuracion_sistema).filter(
        Configuracion_sistema.clave == "gmail_history_id"
    ).first()

    try:
        if IdGuardado:
            history_id = IdGuardado.valor
            IdNuevoMensaje = []
            nuevo_history_id = None
            page_token = None

            while True:
                kwargs = {
                    "userId": "me",
                    "startHistoryId": history_id,
                    "historyTypes": ["messageAdded"],
                }
                if page_token:
                    kwargs["pageToken"] = page_token

                listaHistory = servicio.users().history().list(**kwargs).execute()

                # El historyId mas reciente solo viene en la primera pagina.
                # Guardarlo en cada iteracion sobreescribiria con uno viejo.
                if nuevo_history_id is None:
                    nuevo_history_id = listaHistory.get("historyId")

                for record in listaHistory.get("history", []):
                    for item in record.get("messagesAdded", []):
                        msg_id = item.get("message", {}).get("id")
                        if msg_id:
                            IdNuevoMensaje.append(msg_id)

                page_token = listaHistory.get("nextPageToken")
                if not page_token:
                    break

            return IdNuevoMensaje, nuevo_history_id

        else:
            perfil = servicio.users().getProfile(userId="me").execute()
            history_id = perfil["historyId"]
            IdNuevo = Configuracion_sistema(
                clave="gmail_history_id",
                valor=history_id
            )
            db.add(IdNuevo)
            db.commit()
            db.refresh(IdNuevo)
            return [], None

    except HttpError as error:
        if error.resp.status == 404:
            # El historyId guardado venció (Gmail lo conserva ~1 semana).
            # Se resiembra desde el momento actual: los correos del periodo
            # caído se pierden, pero el sistema queda operativo.
            logger.error(
                "historyId vencido o invalido (%s). Resembrando desde ahora. "
                "Los correos del periodo caido requieren revision manual.",
                error
            )
            try:
                perfil = servicio.users().getProfile(userId="me").execute()
                nuevo_id = perfil["historyId"]
                if IdGuardado:
                    IdGuardado.valor = nuevo_id
                else:
                    db.add(Configuracion_sistema(
                        clave="gmail_history_id",
                        valor=nuevo_id
                    ))
                db.commit()
                logger.info("historyId resembrado: %s", nuevo_id)
            except Exception as e_resemble:
                logger.error("No se pudo resembrar el historyId: %s", e_resemble)
        else:
            logger.error("Error de Gmail API (%s): %s", error.resp.status, error)

        return [], None
TIPOS_ADJUNTO = ('application/pdf', 'application/xml', 'text/xml',
                 'application/octet-stream')


def _recorrer_partes(servicio, message_id, partes, adjuntos):
    """
    Recorre el arbol de partes MIME en profundidad.

    Los correos reenviados y los que traen cuerpo en texto+HTML anidan
    las partes: sin recursion, los adjuntos de segundo nivel son
    invisibles.
    """
    for part in partes or []:
        # Primero bajar: una parte contenedora puede tener adjuntos dentro
        subpartes = part.get('parts')
        if subpartes:
            _recorrer_partes(servicio, message_id, subpartes, adjuntos)

        nombre = part.get('filename') or ''
        mime = part.get('mimeType') or ''
        cuerpo = part.get('body', {})
        adjunto_id = cuerpo.get('attachmentId')

        if not adjunto_id or not nombre:
            continue

        # Algunos servidores mandan XML y PDF como octet-stream.
        # Sin esto se pierden facturas legitimas.
        es_relevante = (
            mime in TIPOS_ADJUNTO
            or nombre.lower().endswith(('.pdf', '.xml'))
        )
        if not es_relevante:
            continue

        if nombre in adjuntos:
            continue

        try:
            adj = servicio.users().messages().attachments().get(
                userId='me', messageId=message_id, id=adjunto_id
            ).execute()
            adjuntos[nombre] = base64.urlsafe_b64decode(adj['data'])
        except HttpError as error:
            logger.warning(
                "No se pudo descargar el adjunto %s de %s: %s",
                nombre, message_id, error
            )


def extraer_adjuntos(servicio, message_id):
    """Devuelve ({nombre: bytes}, asunto). Recorre todo el arbol MIME."""
    mensaje = servicio.users().messages().get(
        userId='me', id=message_id, format='full'
    ).execute()

    payload = mensaje.get('payload', {})

    headers = payload.get('headers', [])
    asunto = next(
        (h['value'] for h in headers if h.get('name', '').lower() == 'subject'),
        ''
    )

    adjuntos: dict[str, bytes] = {}
    _recorrer_partes(servicio, message_id, payload.get('parts'), adjuntos)

    return adjuntos, asunto

def obtener_ultimo_mensaje(servicio):
    resultado = servicio.users().messages().list(
        userId='me',
        maxResults=1
    ).execute()
    
    mensajes = resultado.get('messages', [])
    if mensajes:
        return mensajes[0]['id']
    return None

