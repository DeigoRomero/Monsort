"""
Prueba la lectura hacia atras del buzon con un Gmail falso.

Lo que importa aqui no es el parser —ese ya tiene sus pruebas— sino las tres
formas en que un backfill sale mal: que el rango pierda el ultimo dia, que
reprocese lo que ya estaba, y que mueva el historyId del flujo incremental.

    python3 prueba_backfill_gmail.py
"""
import base64
import os
import sys
from datetime import date, datetime

os.environ.setdefault("DATABASE_URL", "sqlite://")
os.environ.setdefault("SECRET_KEY", "x")
os.environ.setdefault("ALGORITMO", "HS256")
os.environ.setdefault("TOKEN_ACCESO_MIN_EXPIRACION", "60")
os.environ.setdefault("GMAIL_CLIENT_ID", "x")
os.environ.setdefault("GMAIL_CLIENT_SECRET", "x")
os.environ.setdefault("GMAIL_REFRESH_TOKEN", "x")

from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

from app.BaseDeDatos import Base  # noqa: E402
from app.modelos import (  # noqa: F401,E402
    usuario, factura, estados, configuracion, conceptos,
    complemento_pago, orden_compra, cp_documento_relacionado,
    correo_procesado, cliente, solicitud_sat,
)
from app.modelos.usuario import Usuarios  # noqa: E402
from app.modelos.estados import Estados  # noqa: E402
from app.modelos.factura import Facturas  # noqa: E402
from app.modelos.complemento_pago import ComplementosPago  # noqa: E402
from app.modelos.correo_procesado import CorreosProcesados, CorreosFallidos  # noqa: E402
from app.modelos.configuracion import Configuracion_sistema  # noqa: E402
from app.core.config import settings  # noqa: E402
import app.services.backfill_gmail as bg  # noqa: E402

PROPIO = settings.RFC_EMPRESA.upper()
CLIENTE = "XAXX010101000"

fallos = []


def afirmar(condicion, mensaje):
    print(("  ok   " if condicion else "  FALLA ") + mensaje)
    if not condicion:
        fallos.append(mensaje)


# ─────────────────────────────── XML de prueba

UUID_FACTURA = "A1B2C3D4-0000-0000-0000-00000000000{n}"


def xml_factura(n):
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<cfdi:Comprobante xmlns:cfdi="http://www.sat.gob.mx/cfd/4"
 xmlns:tfd="http://www.sat.gob.mx/TimbreFiscalDigital" Version="4.0"
 Serie="A" Folio="{100 + n}" Fecha="2026-08-15T10:00:00" SubTotal="1000.00"
 Total="1160.00" Moneda="MXN" TipoDeComprobante="I">
 <cfdi:Emisor Rfc="{PROPIO}" Nombre="MONSORT" RegimenFiscal="601"/>
 <cfdi:Receptor Rfc="{CLIENTE}" Nombre="CLIENTE DEMO" UsoCFDI="G03"/>
 <cfdi:Conceptos>
  <cfdi:Concepto Descripcion="Servicio" Cantidad="1" ClaveUnidad="E48"
                 ValorUnitario="1000.00" Importe="1000.00"/>
 </cfdi:Conceptos>
 <cfdi:Impuestos TotalImpuestosTrasladados="160.00"/>
 <cfdi:Complemento>
  <tfd:TimbreFiscalDigital UUID="{UUID_FACTURA.format(n=n)}"/>
 </cfdi:Complemento>
</cfdi:Comprobante>""".encode("utf-8")


def xml_cp(uuid_cp, uuid_documento):
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<cfdi:Comprobante xmlns:cfdi="http://www.sat.gob.mx/cfd/4"
 xmlns:pago20="http://www.sat.gob.mx/Pagos20"
 xmlns:tfd="http://www.sat.gob.mx/TimbreFiscalDigital" Version="4.0"
 Serie="CP" Folio="700" Fecha="2026-08-20T10:00:00" TipoDeComprobante="P">
 <cfdi:Emisor Rfc="{PROPIO}" Nombre="MONSORT" RegimenFiscal="601"/>
 <cfdi:Receptor Rfc="{CLIENTE}" Nombre="CLIENTE DEMO" UsoCFDI="CP01"/>
 <cfdi:Complemento>
  <pago20:Pagos Version="2.0">
   <pago20:Pago FechaPago="2026-08-20T00:00:00" FormaDePagoP="03"
                MonedaP="MXN" TipoCambioP="1" Monto="1160.00">
    <pago20:DoctoRelacionado IdDocumento="{uuid_documento.lower()}"
                             NumParcialidad="1" ImpPagado="1160.00"
                             ImpSaldoInsoluto="0.00"/>
   </pago20:Pago>
  </pago20:Pagos>
  <tfd:TimbreFiscalDigital UUID="{uuid_cp}"/>
 </cfdi:Complemento>
</cfdi:Comprobante>""".encode("utf-8")


UUID_CP = "BBBBBBBB-1111-2222-3333-444444444444"

# message_id -> {asunto, adjuntos {nombre: bytes}}
BUZON = {
    "MSG-AGO-1": {"asunto": "Factura agosto", "adjuntos": {"f1.xml": xml_factura(1)}},
    "MSG-AGO-2": {"asunto": "Factura agosto", "adjuntos": {"f2.xml": xml_factura(2)}},
    "MSG-AGO-3": {"asunto": "Complemento de pago",
                  "adjuntos": {"cp.xml": xml_cp(UUID_CP, UUID_FACTURA.format(n=1))}},
    "MSG-AGO-4": {"asunto": "Correo roto", "adjuntos": {"malo.xml": b"<no-es-xml"}},
    "MSG-AGO-5": {"asunto": "Ya procesado antes", "adjuntos": {"f5.xml": xml_factura(5)}},
}


# ─────────────────────────────── Gmail falso

class GmailFalso:
    """Imita lo justo de la API: list, get y attachments.get."""

    def __init__(self, buzon, por_pagina=2):
        self.buzon = buzon
        self.por_pagina = por_pagina
        self.consultas = []
        self.gets = []

    # -- encadenamiento servicio.users().messages()...
    def users(self):
        return self

    def messages(self):
        return self

    def attachments(self):
        return self

    def list(self, userId=None, q=None, maxResults=None, pageToken=None,
             includeSpamTrash=False):
        self.consultas.append(q)
        ids = list(self.buzon)
        inicio = int(pageToken or 0)
        trozo = ids[inicio:inicio + self.por_pagina]
        siguiente = inicio + self.por_pagina
        respuesta = {"messages": [{"id": i} for i in trozo]}
        if siguiente < len(ids):
            respuesta["nextPageToken"] = str(siguiente)
        return _Ejecutable(respuesta)

    def get(self, userId=None, id=None, messageId=None, format=None):
        # attachments().get pasa messageId; messages().get pasa id.
        if messageId is not None:
            nombre = id           # aqui `id` es el attachmentId, que es el nombre
            datos = self.buzon[messageId]["adjuntos"][nombre]
            return _Ejecutable({"data": base64.urlsafe_b64encode(datos).decode()})

        self.gets.append(id)
        correo = self.buzon[id]
        partes = [
            {"filename": nombre, "mimeType": "text/xml",
             "body": {"attachmentId": nombre}}
            for nombre in correo["adjuntos"]
        ]
        return _Ejecutable({
            "payload": {
                "headers": [{"name": "Subject", "value": correo["asunto"]}],
                "parts": partes,
            }
        })


class _Ejecutable:
    def __init__(self, valor):
        self.valor = valor

    def execute(self):
        return self.valor


# ─────────────────────────────── Base

motor = create_engine("sqlite://", connect_args={"check_same_thread": False},
                      poolclass=StaticPool)
Base.metadata.create_all(motor)
db = sessionmaker(bind=motor)()

for nombre in ("Pendiente de CP", "Cancelada", "Requiere captura manual",
               "Pendiente de revision", "Pendiente de revisión",
               "Pendiente de factura", "Revisada", "Histórico",
               "Pendiente de verificación"):
    db.add(Estados(nombre_estado=nombre, descripcion_estado="x"))
db.add(Usuarios(nombre="Sistema Automatico", correo="s@m.test",
                password_hash="x", rol="sistema"))
# El flujo incremental va en esta fila: el backfill no debe tocarla.
db.add(Configuracion_sistema(clave="gmail_history_id", valor="999999"))
# Un correo del rango que ya se habia procesado antes.
db.add(CorreosProcesados(message_id="MSG-AGO-5", tipo_correo="facturas:1",
                         fecha_procesado=datetime(2026, 8, 31)))
db.commit()

servicio = GmailFalso(BUZON)
bg.obtener_servicio_gmail = lambda _db=None: servicio
# Sin pausas: la prueba no necesita esperar 5 segundos reales.
bg.PAUSA_ENTRE_CORREOS = 0


print("[1] El rango incluye el ultimo dia")

q = bg.construir_query(date(2026, 8, 1), date(2026, 8, 31), "has:attachment")
afirmar("after:2026/08/01" in q, f"after: con el primer dia ({q})")
afirmar("before:2026/09/01" in q,
        "before: con el dia SIGUIENTE al ultimo, porque before: es exclusivo "
        f"({q})")
afirmar("has:attachment" in q, "conserva el filtro extra")

sin_extra = bg.construir_query(date(2026, 8, 1), date(2026, 8, 31), "")
afirmar(sin_extra.strip() == "after:2026/08/01 before:2026/09/01",
        f"sin filtro extra no deja espacios sueltos ('{sin_extra}')")


print("\n[2] Listar pagina y no repite")

ids = bg.listar_mensajes(servicio, q)
afirmar(ids == list(BUZON),
        f"trae los 5 correos siguiendo el nextPageToken ({len(ids)})")
afirmar(len(ids) == len(set(ids)), "sin repetidos")


print("\n[3] Solo contar no baja nada")

antes = len(servicio.gets)
reporte = bg.backfill(db, date(2026, 8, 1), date(2026, 8, 31), solo_contar=True)
afirmar(len(servicio.gets) == antes,
        "con --contar no se pide ningun mensaje completo")
afirmar(reporte["encontrados"] == 5, f"cuenta 5 en el buzon ({reporte})")
afirmar(reporte["ya_estaban"] == 1,
        f"y reconoce el que ya estaba procesado ({reporte['ya_estaban']})")
afirmar(reporte["pendientes_del_rango"] == 4,
        f"quedan 4 por procesar ({reporte['pendientes_del_rango']})")


print("\n[4] La corrida real")

reporte = bg.backfill(db, date(2026, 8, 1), date(2026, 8, 31))

afirmar("MSG-AGO-5" not in servicio.gets,
        "no vuelve a bajar el correo que ya estaba procesado")
afirmar(reporte["procesados"] == 4,
        f"procesa los 4 pendientes ({reporte['procesados']})")
# El XML roto NO tumba el correo: procesar_correo() aisla cada documento en
# un savepoint, cuenta el error y sigue. El correo queda en CorreosProcesados
# con el conteo de errores, no en CorreosFallidos. CorreosFallidos es para
# los correos que no se pudieron ni bajar (el 403 de cuota de septiembre).
afirmar(reporte["fallidos"] == 0,
        f"un adjunto ilegible no tira el correo entero ({reporte['fallidos']})")
afirmar(reporte["documentos"]["facturas"] == 2,
        f"2 facturas ({reporte['documentos']})")
afirmar(reporte["documentos"]["complementos"] == 1,
        f"1 complemento ({reporte['documentos']})")

afirmar(db.query(Facturas).count() == 2, "las facturas quedaron en la base")
afirmar(db.query(ComplementosPago).count() == 1, "el CP tambien")

afirmar(db.query(CorreosFallidos).count() == 0,
        "CorreosFallidos queda vacio: nada se cayo al bajarlo")
# Un XML que no se puede parsear ni siquiera llega a procesar_factura():
# clasificar_adjuntos() lo manda a "otros" con una linea de log, asi que el
# correo queda como "desconocido", igual que uno de publicidad. Es el
# comportamiento actual, no un descuido de esta prueba: dejarlo asentado
# aqui para que un cambio futuro no pase inadvertido.
roto = db.query(CorreosProcesados).filter(
    CorreosProcesados.message_id == "MSG-AGO-4").first()
afirmar(roto is not None and roto.tipo_correo == "desconocido",
        f"el correo del XML ilegible queda como 'desconocido' "
        f"({roto.tipo_correo if roto else None})")


print("\n[5] Lo que NO debe tocar")

historial = db.query(Configuracion_sistema).filter(
    Configuracion_sistema.clave == "gmail_history_id").first()
afirmar(historial.valor == "999999",
        f"el historyId del flujo incremental sigue intacto ({historial.valor})")


print("\n[6] Reconcilia lo que acaba de entrar")

from app.modelos.cp_documento_relacionado import CPDocumentosRelacionados  # noqa: E402

doc = db.query(CPDocumentosRelacionados).first()
afirmar(doc is not None and doc.id_factura is not None,
        "el pago del CP quedo pegado a su factura, aunque el IdDocumento "
        "venia en minusculas")


print("\n[7] Correrlo otra vez no duplica nada")

reporte2 = bg.backfill(db, date(2026, 8, 1), date(2026, 8, 31))
afirmar(reporte2["procesados"] == 0,
        f"nada nuevo que procesar ({reporte2['procesados']})")
afirmar(db.query(Facturas).count() == 2, "siguen 2 facturas")
afirmar(db.query(ComplementosPago).count() == 1, "sigue 1 complemento")
afirmar(reporte2["intentados"] == 0,
        f"no reintenta nada: los 5 del rango ya estan en CorreosProcesados "
        f"({reporte2['intentados']})")
afirmar(reporte2["ya_estaban"] == 5,
        f"los cuenta como ya procesados ({reporte2['ya_estaban']})")

db.close()

print()
if fallos:
    print(f"{len(fallos)} PRUEBA(S) FALLIDA(S):")
    for f in fallos:
        print("  -", f)
    sys.exit(1)
print("Todas las pruebas del backfill pasaron.")
