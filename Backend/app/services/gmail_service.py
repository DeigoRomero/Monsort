from app.core.config import settings
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from sqlalchemy.orm import Session
from app.modelos.configuracion import Configuracion_sistema
import base64
from googleapiclient.discovery import build
import xml.etree.ElementTree as ET

def obtener_servicio_gmail():
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
    IdGuardado = db.query(Configuracion_sistema).filter(Configuracion_sistema.clave == "gmail_history_id").first()
    try:
        if IdGuardado:
            history_id = IdGuardado.valor
            listaHistory = servicio.users().history().list(
                userId='me',
                startHistoryId=history_id,
                historyTypes=['messageAdded']
            ).execute()

            cambios = listaHistory.get('history',[])
            IdNuevoMensaje = []

            #Parsear los history records
            for record in cambios:
                mensajes_nuevos = record.get('messagesAdded',[])
                for item in mensajes_nuevos:
                    mensaje = item.get('message',[])
                    IdNuevoMensaje.append(mensaje.get('id'))
            nuevo_history_id = listaHistory.get('historyId')
            IdGuardado.valor = nuevo_history_id
            db.commit()
            return IdNuevoMensaje
        else:
            perfil = servicio.users().getProfile(userId='me').execute()
            history_id = perfil['historyId']
            IdNuevo = Configuracion_sistema(
                clave="gmail_history_id",
                valor=history_id
            )

            db.add(IdNuevo)
            db.commit()
            db.refresh(IdNuevo)
            return []
    except HttpError as error:
        print(error)   

def extraer_adjuntos(servicio, message_id):
    """
    Extrae los adjuntos (PDF, XML) de un mensaje de Gmail.
    Regresa un dict con {nombre_archivo: contenido_bytes}
    """

    Mensaje_completo = servicio.users().messages().get(userId='me', id=message_id, format='full').execute()
    partes = Mensaje_completo.get('payload',{}).get('parts',[])

    if partes:
        adjuntos_encontrados = {}
        for part in partes:
            mime_type = part.get("mimeType")
            nombre_archivo = part.get("filename")
            part_body = part.get("body",{})
            data = part_body.get("data")
            adjunto_id = part_body.get('attachmentId')

            if adjunto_id:
                if mime_type in ['application/pdf','application/xml','text/xml']:
                    adj = servicio.users().messages().attachments().get(
                        userId='me',
                        messageId=message_id,
                        id=adjunto_id
                        ).execute()
                    archivo_data = base64.urlsafe_b64decode(adj['data'])
                    adjuntos_encontrados[nombre_archivo] = archivo_data
            elif data and mime_type in ['text/plain', 'text/html']:
                print(f"Body: {base64.urlsafe_b64decode(data).decode('utf-8')}")
            
        return adjuntos_encontrados
    else:
        return {}

def obtener_ultimo_mensaje(servicio):
    resultado = servicio.users().messages().list(
        userId='me',
        maxResults=1
    ).execute()
    
    mensajes = resultado.get('messages', [])
    if mensajes:
        return mensajes[0]['id']
    return None

