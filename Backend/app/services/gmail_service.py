from app.core.config import settings
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from sqlalchemy.orm import Session
from app.modelos.configuracion import Configuracion_sistema
import base64
import xml.etree.ElementTree as ET
import logging
import time

logger = logging.getLogger(__name__)

TIPOS_ADJUNTO = ('application/pdf', 'application/xml', 'text/xml',
                 'application/octet-stream',
                 'application/zip', 'application/x-zip-compressed')

EXTENSIONES_RELEVANTES = ('.pdf', '.xml', '.zip')

# Gmail cobra cuota por llamada y cada adjunto es una llamada aparte.
# Un correo con doce adjuntos agota el limite por minuto sin pausa.
PAUSA_ENTRE_ADJUNTOS = 0.3
INTENTOS_ADJUNTO = 4
ESTADOS_TRANSITORIOS = (403, 429, 500, 502, 503, 504)

# 404 = el mensaje ya no existe (borrado, purgado de la papelera). Reintentar
# no lo revive: se marca como permanente para que el job de reproceso no
# gaste cuota en el.
ESTADOS_PERMANENTES = (400, 404)


def es_error_permanente(excepcion: Exception) -> bool:
    """True si reintentar este error de Gmail no puede cambiar el resultado."""
    resp = getattr(excepcion, "resp", None)
    estado = getattr(resp, "status", None)
    return estado in ESTADOS_PERMANENTES


def _con_reintentos(operacion, descripcion: str):
    """
    Ejecuta una llamada a Gmail reintentando ante errores transitorios.

    NO captura el fallo final a proposito: la excepcion debe llegar hasta
    procesar_correos_nuevos() para que el correo quede en CorreosFallidos y
    se pueda reprocesar. Tragarse el error aqui significaria marcar el correo
    como procesado con datos faltantes, y como el historyId ya avanzo, esa
    factura se perderia para siempre.
    """
    for intento in range(INTENTOS_ADJUNTO):
        try:
            return operacion()

        except HttpError as error:
            ultimo = intento == INTENTOS_ADJUNTO - 1
            if error.resp.status not in ESTADOS_TRANSITORIOS or ultimo:
                raise

            espera = 2 ** intento      # 1, 2, 4 segundos
            logger.warning(
                "Gmail %s en %s. Reintento %d/%d en %ds",
                error.resp.status, descripcion,
                intento + 1, INTENTOS_ADJUNTO - 1, espera,
            )
            time.sleep(espera)


def obtener_servicio_gmail(db: Session | None = None):
    # settings es el valor por defecto; la BD lo sobreescribe si existe.
    # La BD es la fuente de verdad: /auth/gmail/iniciar escribe ahi.
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
        refresh_token=refresh_token,
        token_uri="https://oauth2.googleapis.com/token",
    )
    servicio = build('gmail', 'v1', credentials=creds)
    return servicio


def obtener_mensajes_nuevos(db: Session, servicio=None):
    # El servicio se recibe ya construido para no hacer un segundo
    # build() + refresh de token en cada ciclo del scheduler.
    if servicio is None:
        servicio = obtener_servicio_gmail(db)

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


def _descargar_adjunto(servicio, message_id, adjunto_id, nombre):
    """Descarga un adjunto reintentando ante errores transitorios."""
    def operacion():
        adj = servicio.users().messages().attachments().get(
            userId='me', messageId=message_id, id=adjunto_id
        ).execute()
        return base64.urlsafe_b64decode(adj['data'])

    return _con_reintentos(operacion, f"bajar {nombre} de {message_id}")


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
            or nombre.lower().endswith(EXTENSIONES_RELEVANTES)
        )
        if not es_relevante:
            continue

        if nombre in adjuntos:
            continue

        adjuntos[nombre] = _descargar_adjunto(
            servicio, message_id, adjunto_id, nombre
        )

        # Espaciar las llamadas: la cuota de Gmail es por minuto y por
        # usuario, y un correo con muchos adjuntos la agota de golpe.
        time.sleep(PAUSA_ENTRE_ADJUNTOS)


def extraer_adjuntos(servicio, message_id):
    """Devuelve ({nombre: bytes}, asunto). Recorre todo el arbol MIME."""
    # ESTA es la llamada que tiraba ~1,700 correos entre el 11 y el 26 de
    # septiembre de 2026: los adjuntos tenian backoff y este get no, asi que
    # un 403 por cuota bastaba para dar el correo por perdido. Blindar la
    # bodega y dejar el porton abierto.
    mensaje = _con_reintentos(
        lambda: servicio.users().messages().get(
            userId='me', id=message_id, format='full'
        ).execute(),
        f"leer mensaje {message_id}",
    )

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