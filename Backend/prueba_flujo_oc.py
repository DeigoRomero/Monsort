"""
Flujo completo de OCs desde el correo, con los casos que reportó el cliente
el 29/09/2026 (PDFs en pruebas_oc/), y las notificaciones del ciclo.

Corre contra DATABASE_URL dentro de una transacción que se revierte al final:
no deja nada en la base.

    python prueba_flujo_oc.py
"""
import base64
import sys
from pathlib import Path

from app.BaseDeDatos import SessionLocal
from app.modelos.notificacion import Notificaciones
from app.modelos.orden_compra import OrdenesCompra
from app.services.factura_service import procesar_correo
from app.services.gmail_service import extraer_correo
from app.services.notificacion_service import instantanea, registrar_revision_gmail
from app.services.usuario_service import obtener_usuario_sistema

CARPETA = Path(__file__).parent / "pruebas_oc"
fallos = []


def afirmar(cond, msg):
    print(("  ok    " if cond else "  FALLA ") + msg)
    if not cond:
        fallos.append(msg)


HTML_SCIQUEST = """<html><body><table>
<tr><td>Purchase Order</td></tr>
<tr><td>Purchase Order Date</td><td>PO/Reference No.</td><td>Revision No.</td></tr>
<tr><td>29 Sep 2026</td><td>D1009036</td><td>0</td></tr>
<tr><td>Requester Information</td><td>Contact</td><td>Mario Gonzalez</td></tr>
<tr><td>Supplier Name</td><td>MONSORT SIN FRONTERAS SA DE CV</td></tr>
<tr><td>Requested Delivery Date</td><td>25/09/2026</td></tr>
<tr><td>Total</td><td>1,414.00 USD</td></tr></table></body></html>"""


class _Ejecutable:
    def __init__(self, valor):
        self.valor = valor

    def execute(self):
        return self.valor


class GmailFalso:
    """Lo mínimo de la API de Gmail que usa extraer_correo()."""
    def __init__(self, mensaje):
        self.mensaje = mensaje

    def users(self):
        return self

    def messages(self):
        return self

    def get(self, **_):
        return _Ejecutable(self.mensaje)

    def attachments(self):
        return self


def b64(texto: str) -> str:
    return base64.urlsafe_b64encode(texto.encode()).decode()


db = SessionLocal()
db.begin()
try:
    sistema = obtener_usuario_sistema(db)

    print("[1] El cuerpo del correo se lee (SciQuest manda solo HTML)")
    mensaje = {"payload": {
        "mimeType": "multipart/alternative",
        "headers": [{"name": "Subject", "value": "Regal Rexnord Procurement, PO#: D1009036"},
                    {"name": "From", "value": "support@sciquest.com"}],
        "parts": [
            {"mimeType": "text/plain", "body": {"data": b64("")}},
            {"mimeType": "text/html", "body": {"data": b64(HTML_SCIQUEST)}},
        ],
    }}
    correo = extraer_correo(GmailFalso(mensaje), "msg-regal")
    afirmar(correo["adjuntos"] == {}, "sin adjuntos")
    afirmar("D1009036" in correo["cuerpo"] and "MONSORT" in correo["cuerpo"],
            "el HTML se convierte a texto con el número y el proveedor")

    antes = instantanea(db)

    print("\n[2] OC en el cuerpo -> se guarda con PDF de evidencia")
    r = procesar_correo(correo["adjuntos"], correo["asunto"], "prueba-regal", db, sistema,
                        cuerpo=correo["cuerpo"], remitente=correo["remitente"])
    oc = db.query(OrdenesCompra).filter(OrdenesCompra.message_id == "prueba-regal").first()
    afirmar(r["ordenes"] == 1 and oc is not None, "se creó 1 orden de compra")
    afirmar(oc and oc.numero_oc == "D1009036", f"número D1009036 ({oc and oc.numero_oc})")
    afirmar(oc and oc.archivo and oc.archivo[:4] == b"%PDF", "con PDF generado del cuerpo")
    r = procesar_correo({}, correo["asunto"], "prueba-regal-2", db, sistema,
                        cuerpo=correo["cuerpo"], remitente=correo["remitente"])
    afirmar(r["ordenes"] == 0, "el recordatorio con la misma OC no la duplica")
    r = procesar_correo({}, "RE: " + correo["asunto"], "prueba-regal-3", db, sistema,
                        cuerpo=correo["cuerpo"], remitente="mario@regalrexnord.com")
    afirmar(r["ordenes"] == 0, "una respuesta (RE:) no crea OC")

    print("\n[3] PDFs adjuntos que antes salían mal")
    casos = [("PO-NHY13E2871.pdf", "Purchase Order NHY13E2871", "NHY13E2871"),
             ("OC_GRUPOMONSORT_CL-0039.pdf", "Orden de compra", "CL-0039")]
    for nombre, asunto, esperado in casos:
        # El asunto es un texto genérico a propósito: el número sale del PDF.
        # Los PDFs son documentos reales de clientes: viven solo en la máquina
        # de desarrollo (Backend/pruebas_oc/, fuera de git).
        if not (CARPETA / nombre).exists():
            print(f"  (se omite {nombre}: no está en {CARPETA})")
            continue
        pdf = (CARPETA / nombre).read_bytes()
        mid = f"prueba-{nombre}"
        procesar_correo({nombre: pdf}, "Nueva orden", mid, db, sistema, cuerpo="")
        oc = db.query(OrdenesCompra).filter(OrdenesCompra.message_id == mid).first()
        afirmar(oc is not None and oc.numero_oc == esperado,
                f"{nombre}: {oc and oc.numero_oc!r} (antes salía '2026' / '0039'), "
                f"confianza {oc and oc.confianza_oc}")

    print("\n[4] Notificación del ciclo")
    n = registrar_revision_gmail(db, antes)
    afirmar(n is not None and "órdenes de compra" in n.titulo or "orden de compra" in (n.titulo if n else ""),
            f"título: {n and n.titulo}")
    afirmar(n is not None and "D1009036" in (n.detalle or ""), "el detalle lista los números")
    antes = instantanea(db)
    afirmar(registrar_revision_gmail(db, antes) is None, "un ciclo sin cambios no genera notificación")
finally:
    db.rollback()
    # registrar_revision_gmail hace commit; se limpia lo que haya quedado.
    for mid in ("prueba-regal", "prueba-PO-NHY13E2871.pdf", "prueba-OC_GRUPOMONSORT_CL-0039.pdf"):
        db.query(OrdenesCompra).filter(OrdenesCompra.message_id == mid).delete()
    db.query(Notificaciones).filter(Notificaciones.detalle.like("%D1009036%")).delete(
        synchronize_session=False)
    db.commit()
    db.close()

print()
if fallos:
    print(f"{len(fallos)} FALLA(S)")
    sys.exit(1)
print("Todas las pruebas del flujo de OC pasaron.")
