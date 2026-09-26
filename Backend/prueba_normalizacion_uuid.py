"""
Pruebas de los dos arreglos del 26/09/2026 por la tarde:

  1. normalizar_uuid(): el UUID se guarda siempre en MAYUSCULAS sin espacios,
     para que un emisor que manda el IdDocumento en minusculas no deje el pago
     huerfano.
  2. reconciliar() separa vincular de recalcular estado: un CP contra una
     factura del historico ahora SI queda vinculado y SI marca
     fecha_liquidacion, pero el estado 'Historico' no se toca.

    python3 prueba_normalizacion_uuid.py
"""
import os
import sys
from datetime import datetime

os.environ.setdefault("DATABASE_URL", "sqlite://")
for clave, valor in {
    "SECRET_KEY": "x", "ALGORITMO": "HS256",
    "TOKEN_ACCESO_MIN_EXPIRACION": "60", "GMAIL_CLIENT_ID": "x",
    "GMAIL_CLIENT_SECRET": "x", "GMAIL_REFRESH_TOKEN": "x",
}.items():
    os.environ.setdefault(clave, valor)

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.BaseDeDatos import Base
from app.modelos import (  # noqa: F401
    usuario, factura, estados, configuracion, conceptos,
    complemento_pago, orden_compra, cp_documento_relacionado,
    correo_procesado, cliente, solicitud_sat,
)
from app.modelos.usuario import Usuarios
from app.modelos.estados import Estados
from app.modelos.factura import Facturas
from app.modelos.complemento_pago import ComplementosPago
from app.modelos.cp_documento_relacionado import CPDocumentosRelacionados
from app.services import factura_service as fs

RFC_EMPRESA = "MSF140227BF7"
fallos = []


def afirmar(condicion, mensaje):
    print(("  ok   " if condicion else "  FALLA ") + mensaje)
    if not condicion:
        fallos.append(mensaje)


# ─────────────────────────────── 1. normalizar_uuid

print("\n[1] normalizar_uuid")

afirmar(fs.normalizar_uuid("aabbccdd-1122-3344-5566-778899aabbcc")
        == "AABBCCDD-1122-3344-5566-778899AABBCC",
        "sube a mayusculas")
afirmar(fs.normalizar_uuid("  AABBCCDD-1122-3344-5566-778899AABBCC \n")
        == "AABBCCDD-1122-3344-5566-778899AABBCC",
        "recorta espacios y saltos de linea")
afirmar(fs.normalizar_uuid(None) is None, "None se queda None")
afirmar(fs.normalizar_uuid("   ") is None, "una cadena de espacios se vuelve None")


# ─────────────────────────────── 2. Los parsers normalizan

print("\n[2] Los parsers guardan el UUID normalizado")

UUID_MINUSCULAS = "0f4219bc-f1a6-470a-8e3a-8c08e50917cb"
UUID_MAYUSCULAS = UUID_MINUSCULAS.upper()


def xml_factura(uuid, folio):
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<cfdi:Comprobante xmlns:cfdi="http://www.sat.gob.mx/cfd/4"
  xmlns:tfd="http://www.sat.gob.mx/TimbreFiscalDigital"
  Version="4.0" Serie="A" Folio="{folio}" Fecha="2026-03-10T10:00:00"
  SubTotal="1000.00" Total="1160.00" Moneda="MXN" TipoDeComprobante="I">
  <cfdi:Emisor Rfc="{RFC_EMPRESA}" Nombre="GRUPO MONSORT"/>
  <cfdi:Receptor Rfc="XAXX010101000" Nombre="CLIENTE DEMO"/>
  <cfdi:Conceptos>
    <cfdi:Concepto Descripcion="Servicio contra OC 4501" Cantidad="1"
      ClaveUnidad="E48" ValorUnitario="1000.00" Importe="1000.00"/>
  </cfdi:Conceptos>
  <cfdi:Impuestos TotalImpuestosTrasladados="160.00"/>
  <cfdi:Complemento><tfd:TimbreFiscalDigital UUID="{uuid}"/></cfdi:Complemento>
</cfdi:Comprobante>"""


def xml_cp(uuid_cp, folio, uuid_factura):
    """El IdDocumento va en minusculas, como lo manda el PAC problematico."""
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<cfdi:Comprobante xmlns:cfdi="http://www.sat.gob.mx/cfd/4"
  xmlns:pago20="http://www.sat.gob.mx/Pagos20"
  xmlns:tfd="http://www.sat.gob.mx/TimbreFiscalDigital"
  Version="4.0" Serie="CP" Folio="{folio}" TipoDeComprobante="P">
  <cfdi:Complemento>
    <pago20:Pagos Version="2.0">
      <pago20:Pago FechaPago="2026-04-15T00:00:00" FormaDePagoP="03"
        MonedaP="MXN" Monto="1160.00">
        <pago20:DoctoRelacionado IdDocumento="{uuid_factura}"
          NumParcialidad="1" ImpPagado="1160.00" ImpSaldoInsoluto="0"/>
      </pago20:Pago>
    </pago20:Pagos>
    <tfd:TimbreFiscalDigital UUID="{uuid_cp}"/>
  </cfdi:Complemento>
</cfdi:Comprobante>"""


datos_f = fs.extraer_datos_xml(xml_factura(UUID_MINUSCULAS, "100").encode())
afirmar(datos_f["folio_fiscal"] == UUID_MAYUSCULAS,
        "la factura normaliza su folio_fiscal")

datos_cp = fs.extraer_datos_cp(
    xml_cp("aaaa1111-2222-3333-4444-555566667777", "CP1", UUID_MINUSCULAS).encode()
)
afirmar(datos_cp["uuid_cp"] == "AAAA1111-2222-3333-4444-555566667777",
        "el CP normaliza su propio uuid_cp")
afirmar(datos_cp["documentos"][0]["uuid_documento"] == UUID_MAYUSCULAS,
        "el CP normaliza el IdDocumento del DoctoRelacionado")


# ─────────────────────────────── 3. El caso real: caja distinta

print("\n[3] Factura en mayusculas + CP en minusculas se encuentran")

motor = create_engine("sqlite://")
Base.metadata.create_all(motor)
db = sessionmaker(bind=motor)()

estados = {}
for nombre in ("Pendiente de factura", "Pendiente de CP", "Pendiente de revisión",
               "Requiere captura manual", "Cancelada", "Revisada", "Histórico",
               "Pendiente de verificación"):
    e = Estados(nombre_estado=nombre, descripcion_estado="x")
    db.add(e)
    estados[nombre] = e
usuario_sistema = Usuarios(nombre="Sistema Automatico", correo="s@m.test",
                           password_hash="x", rol="sistema")
db.add(usuario_sistema)
db.commit()

fs.settings.RFC_EMPRESA = RFC_EMPRESA

# La factura entra por correo (se guarda en MAYUSCULAS)
fs.procesar_correo(
    {"f.xml": xml_factura(UUID_MINUSCULAS, "100").encode()},
    asunto="Factura", mensaje_id="MSG-F", db=db, usuario_sistema=usuario_sistema,
)
# El CP la referencia en minusculas
fs.procesar_correo(
    {"cp.xml": xml_cp("aaaa1111-2222-3333-4444-555566667777", "CP1",
                      UUID_MINUSCULAS).encode()},
    asunto="Complemento", mensaje_id="MSG-CP", db=db,
    usuario_sistema=usuario_sistema,
)
db.commit()

fs.reconciliar(db)

doc = db.query(CPDocumentosRelacionados).first()
afirmar(doc.id_factura is not None,
        "el pago quedo vinculado a su factura pese a la diferencia de caja")

factura = db.query(Facturas).filter(Facturas.folio_fiscal == UUID_MAYUSCULAS).first()
afirmar(factura.fecha_liquidacion is not None,
        "y marco fecha_liquidacion")


# ─────────────────────────────── 4. Facturas del historico

print("\n[4] Un CP contra una factura del historico")

historica = Facturas(
    folio_fiscal="DDDDDDDD-1111-2222-3333-444444444444",
    folio_interno="H-500", rfc="XAXX010101000", cliente="CLIENTE VIEJO",
    fecha=datetime(2026, 2, 10).date(), total=5000, moneda="MXN",
    origen="excel",
    id_usuario=usuario_sistema.id_usuario,
    id_estado=estados["Histórico"].id_estado,
)
db.add(historica)
db.commit()
id_historica = historica.id_factura

fs.procesar_correo(
    {"cp2.xml": xml_cp("bbbb2222-3333-4444-5555-666677778888", "CP2",
                       "dddddddd-1111-2222-3333-444444444444").encode()},
    asunto="Complemento del historico", mensaje_id="MSG-CP2", db=db,
    usuario_sistema=usuario_sistema,
)
db.commit()

fs.reconciliar(db)
db.expire_all()

historica = db.query(Facturas).filter(Facturas.id_factura == id_historica).first()
doc_hist = (
    db.query(CPDocumentosRelacionados)
    .filter(CPDocumentosRelacionados.uuid_documento
            == "DDDDDDDD-1111-2222-3333-444444444444")
    .first()
)

afirmar(doc_hist.id_factura == id_historica,
        "el pago SI se vincula a una factura del historico (antes se ignoraba)")
afirmar(historica.fecha_liquidacion is not None,
        "y le escribe fecha_liquidacion, que es un hecho fiscal")
afirmar(historica.id_estado == estados["Histórico"].id_estado,
        "pero NO le cambia el estado: 'Historico' es una decision tomada")


# ─────────────────────────────── 5. Las fronteras que siguen en pie

print("\n[5] Lo que reconciliar() sigue respetando")

cancelada = Facturas(
    folio_fiscal="CCCCCCCC-1111-2222-3333-444444444444",
    folio_interno="C-900", rfc="XAXX010101000", cliente="CLIENTE",
    fecha=datetime(2026, 5, 1).date(), total=100, moneda="MXN",
    id_usuario=usuario_sistema.id_usuario,
    id_estado=estados["Cancelada"].id_estado,
)
db.add(cancelada)
db.commit()
id_cancelada = cancelada.id_factura

fs.reconciliar(db)
db.expire_all()

cancelada = db.query(Facturas).filter(Facturas.id_factura == id_cancelada).first()
afirmar(cancelada.id_estado == estados["Cancelada"].id_estado,
        "una factura cancelada conserva su estado")

# Un CP cancelado no debe liquidar nada
cp_malo = ComplementosPago(
    uuid_cp="EEEEEEEE-1111-2222-3333-444444444444", folio="CP9",
    fecha_pago=datetime(2026, 6, 1), moneda="MXN", tipo_cambio=1, monto=100,
    message_id="MSG-CP9", cancelado=True,
)
db.add(cp_malo)
db.commit()
db.add(CPDocumentosRelacionados(
    id_complemento=cp_malo.id, uuid_documento=cancelada.folio_fiscal,
    num_parcialidad=1, imp_pagado=100, imp_saldo_insoluto=0, id_factura=None,
))
db.commit()

fs.reconciliar(db)
db.expire_all()

doc_malo = db.query(CPDocumentosRelacionados).filter(
    CPDocumentosRelacionados.id_complemento == cp_malo.id
).first()
afirmar(doc_malo.id_factura is None,
        "un CP cancelado sigue sin vincularse")

cancelada = db.query(Facturas).filter(Facturas.id_factura == id_cancelada).first()
afirmar(cancelada.fecha_liquidacion is None,
        "y no liquida nada")

# Idempotencia
antes = [(f.id_factura, f.id_estado, f.fecha_liquidacion)
         for f in db.query(Facturas).order_by(Facturas.id_factura).all()]
fs.reconciliar(db)
db.expire_all()
despues = [(f.id_factura, f.id_estado, f.fecha_liquidacion)
           for f in db.query(Facturas).order_by(Facturas.id_factura).all()]
afirmar(antes == despues, "reconciliar() sigue siendo idempotente")

db.close()

print()
if fallos:
    print(f"{len(fallos)} PRUEBA(S) FALLIDA(S):")
    for f in fallos:
        print("  -", f)
    sys.exit(1)
print("Todas las pruebas pasaron.")
