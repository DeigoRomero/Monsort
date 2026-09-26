"""
Pruebas del parche de CPs contra una SQLite en memoria.

No sustituye la prueba en el VPS, pero cubre lo que no se puede ver leyendo:
que el savepoint por documento aisla de verdad, que el ZIP se abre, que el CP
de Pagos10 ya no sale vacio y que clasificar_adjuntos no revienta con un
adjunto raro.

    python3 prueba_parche_cps.py
"""
import io
import os
import sys
import zipfile

os.environ.setdefault("DATABASE_URL", "sqlite://")
os.environ.setdefault("SECRET_KEY", "x")
os.environ.setdefault("ALGORITMO", "HS256")
os.environ.setdefault("TOKEN_ACCESO_MIN_EXPIRACION", "60")
os.environ.setdefault("GMAIL_CLIENT_ID", "x")
os.environ.setdefault("GMAIL_CLIENT_SECRET", "x")
os.environ.setdefault("GMAIL_REFRESH_TOKEN", "x")

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.BaseDeDatos import Base
from app.modelos import (  # noqa: F401  registra todas las tablas
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


# ─────────────────────────────── XML de prueba

def xml_factura(uuid, folio, total="1160.00", oc="OC 4501"):
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<cfdi:Comprobante xmlns:cfdi="http://www.sat.gob.mx/cfd/4"
  xmlns:tfd="http://www.sat.gob.mx/TimbreFiscalDigital"
  Version="4.0" Serie="A" Folio="{folio}" Fecha="2026-09-20T10:00:00"
  SubTotal="1000.00" Total="{total}" Moneda="MXN" TipoDeComprobante="I">
  <cfdi:Emisor Rfc="{RFC_EMPRESA}" Nombre="GRUPO MONSORT"/>
  <cfdi:Receptor Rfc="XAXX010101000" Nombre="CLIENTE DEMO"/>
  <cfdi:Conceptos>
    <cfdi:Concepto Descripcion="Servicio contra {oc}" Cantidad="1"
      ClaveUnidad="E48" ValorUnitario="1000.00" Importe="1000.00"/>
  </cfdi:Conceptos>
  <cfdi:Impuestos TotalImpuestosTrasladados="160.00"/>
  <cfdi:Complemento>
    <tfd:TimbreFiscalDigital UUID="{uuid}"/>
  </cfdi:Complemento>
</cfdi:Comprobante>"""


def xml_cp_pagos20(uuid, folio, uuid_factura, saldo="0"):
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<cfdi:Comprobante xmlns:cfdi="http://www.sat.gob.mx/cfd/4"
  xmlns:pago20="http://www.sat.gob.mx/Pagos20"
  xmlns:tfd="http://www.sat.gob.mx/TimbreFiscalDigital"
  Version="4.0" Serie="CP" Folio="{folio}" Fecha="2026-09-22T09:00:00"
  TipoDeComprobante="P" Moneda="XXX" Total="0">
  <cfdi:Emisor Rfc="{RFC_EMPRESA}" Nombre="GRUPO MONSORT"/>
  <cfdi:Receptor Rfc="XAXX010101000" Nombre="CLIENTE DEMO"/>
  <cfdi:Complemento>
    <pago20:Pagos Version="2.0">
      <pago20:Pago FechaPago="2026-09-22T00:00:00" FormaDePagoP="03"
        MonedaP="MXN" Monto="1160.00">
        <pago20:DoctoRelacionado IdDocumento="{uuid_factura}" NumParcialidad="1"
          ImpPagado="1160.00" ImpSaldoInsoluto="{saldo}"/>
      </pago20:Pago>
    </pago20:Pagos>
    <tfd:TimbreFiscalDigital UUID="{uuid}"/>
  </cfdi:Complemento>
</cfdi:Comprobante>"""


def xml_cp_pagos10(uuid, folio, uuid_factura):
    """CFDI 3.3 con Pagos 1.0. Antes salia con fecha_pago=None."""
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<cfdi:Comprobante xmlns:cfdi="http://www.sat.gob.mx/cfd/3"
  xmlns:pago10="http://www.sat.gob.mx/Pagos"
  xmlns:tfd="http://www.sat.gob.mx/TimbreFiscalDigital"
  Version="3.3" Serie="CP" Folio="{folio}" Fecha="2026-09-22T09:00:00"
  TipoDeComprobante="P" Moneda="XXX" Total="0">
  <cfdi:Complemento>
    <pago10:Pagos Version="1.0">
      <pago10:Pago FechaPago="2026-09-21T00:00:00" FormaDePagoP="03"
        MonedaP="MXN" Monto="500.00">
        <pago10:DoctoRelacionado IdDocumento="{uuid_factura}" NumParcialidad="1"
          ImpPagado="500.00" ImpSaldoInsoluto="660.00"/>
      </pago10:Pago>
    </pago10:Pagos>
    <tfd:TimbreFiscalDigital UUID="{uuid}"/>
  </cfdi:Complemento>
</cfdi:Comprobante>"""


def xml_cp_dos_pagos(uuid, folio, uuid_a, uuid_b):
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<cfdi:Comprobante xmlns:cfdi="http://www.sat.gob.mx/cfd/4"
  xmlns:pago20="http://www.sat.gob.mx/Pagos20"
  xmlns:tfd="http://www.sat.gob.mx/TimbreFiscalDigital"
  Version="4.0" Serie="CP" Folio="{folio}" TipoDeComprobante="P">
  <cfdi:Complemento>
    <pago20:Pagos Version="2.0">
      <pago20:Pago FechaPago="2026-09-20T00:00:00" FormaDePagoP="03"
        MonedaP="MXN" Monto="100.00">
        <pago20:DoctoRelacionado IdDocumento="{uuid_a}" NumParcialidad="1"
          ImpPagado="100.00" ImpSaldoInsoluto="0"/>
      </pago20:Pago>
      <pago20:Pago FechaPago="2026-09-23T00:00:00" FormaDePagoP="02"
        MonedaP="MXN" Monto="250.50">
        <pago20:DoctoRelacionado IdDocumento="{uuid_b}" NumParcialidad="1"
          ImpPagado="250.50" ImpSaldoInsoluto="0"/>
      </pago20:Pago>
    </pago20:Pagos>
    <tfd:TimbreFiscalDigital UUID="{uuid}"/>
  </cfdi:Complemento>
</cfdi:Comprobante>"""


def xml_cp_sin_timbre(folio):
    return f"""<?xml version="1.0"?>
<cfdi:Comprobante xmlns:cfdi="http://www.sat.gob.mx/cfd/4"
  xmlns:pago20="http://www.sat.gob.mx/Pagos20"
  Serie="CP" Folio="{folio}" TipoDeComprobante="P">
  <cfdi:Complemento>
    <pago20:Pagos><pago20:Pago FechaPago="2026-09-22T00:00:00"
      MonedaP="MXN" Monto="10.00"/></pago20:Pagos>
  </cfdi:Complemento>
</cfdi:Comprobante>"""


def pdf_minimo(texto):
    """PDF valido minimo con una capa de texto extraible por pdfplumber."""
    contenido = f"BT /F1 10 Tf 40 700 Td ({texto}) Tj ET"
    objetos = [
        "1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj",
        "2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj",
        "3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 612 792]"
        "/Resources<</Font<</F1 5 0 R>>>>/Contents 4 0 R>>endobj",
        f"4 0 obj<</Length {len(contenido)}>>stream\n{contenido}\nendstream endobj",
        "5 0 obj<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>endobj",
    ]
    salida = "%PDF-1.4\n"
    posiciones = []
    for obj in objetos:
        posiciones.append(len(salida))
        salida += obj + "\n"
    inicio_tabla = len(salida)
    salida += f"xref\n0 {len(objetos) + 1}\n0000000000 65535 f \n"
    for pos in posiciones:
        salida += f"{pos:010d} 00000 n \n"
    salida += (
        f"trailer<</Size {len(objetos) + 1}/Root 1 0 R>>\n"
        f"startxref\n{inicio_tabla}\n%%EOF"
    )
    return salida.encode("latin-1")


# ─────────────────────────────── 1. Parser de CP

print("\n[1] extraer_datos_cp")

UUID_F1 = "11111111-1111-1111-1111-111111111111"
UUID_F2 = "22222222-2222-2222-2222-222222222222"

datos = fs.extraer_datos_cp(xml_cp_pagos20("AAA", "CP576", UUID_F1).encode())
afirmar(datos["uuid_cp"] == "AAA", "Pagos20: saca el UUID del timbre")
afirmar(datos["fecha_pago"] == "2026-09-22T00:00:00", "Pagos20: saca FechaPago")
afirmar(str(datos["monto"]) == "1160.00", "Pagos20: saca el Monto")
afirmar(datos["tipo_cambio"] == "1", "Pagos20: MXN fija tipo_cambio en '1'")
afirmar(datos["documentos"][0]["uuid_documento"] == UUID_F1,
        "Pagos20: saca el DoctoRelacionado")

datos10 = fs.extraer_datos_cp(xml_cp_pagos10("BBB", "CP577", UUID_F1).encode())
afirmar(datos10["fecha_pago"] == "2026-09-21T00:00:00",
        "Pagos10 (CFDI 3.3): YA saca FechaPago (antes era None)")
afirmar(len(datos10["documentos"]) == 1,
        "Pagos10: YA saca el DoctoRelacionado (antes quedaba vacio)")

dos = fs.extraer_datos_cp(xml_cp_dos_pagos("CCC", "CP578", UUID_F1, UUID_F2).encode())
afirmar(str(dos["monto"]) == "350.50",
        "Dos nodos Pago: suma los montos (antes se quedaba con el ultimo)")
afirmar(dos["fecha_pago"] == "2026-09-20T00:00:00",
        "Dos nodos Pago: fecha del primero")
afirmar(len(dos["documentos"]) == 2, "Dos nodos Pago: conserva ambos documentos")


# ─────────────────────────────── 2. Clasificacion y ZIP

print("\n[2] clasificar_adjuntos")

buffer = io.BytesIO()
with zipfile.ZipFile(buffer, "w") as z:
    z.writestr("CP999.xml", xml_cp_pagos20("ZIP1", "CP999", UUID_F1))
    z.writestr("CP999.pdf", pdf_minimo("Complemento de pago ZIP1"))
    z.writestr("leeme.txt", "basura")
zip_bytes = buffer.getvalue()

clasificados = fs.clasificar_adjuntos({
    "factura.xml": xml_factura(UUID_F1, "100").encode(),
    "factura.pdf": pdf_minimo(f"Factura {UUID_F1}"),
    "firma.png": b"\x89PNG\r\n\x1a\n no es xml ni pdf",   # antes: UnboundLocalError
    "paquete.zip": zip_bytes,
})
afirmar(len(clasificados["facturas"]) == 1, "clasifica la factura (TipoDeComprobante=I)")
afirmar(len(clasificados["complementos"]) == 1,
        "clasifica el CP que venia DENTRO del zip")
afirmar("paquete.zip:CP999.pdf" in clasificados["pdfs"],
        "extrae tambien el PDF del zip")
afirmar("firma.png" in clasificados["otros"],
        "el adjunto que no es XML ni PDF ya no levanta UnboundLocalError")

roto = fs.clasificar_adjuntos({"malo.xml": b"<no cierra"})
afirmar(roto["otros"] == ["malo.xml"], "un XML ilegible no tumba la clasificacion")


# ─────────────────────────────── 3. procesar_correo con savepoints

print("\n[3] procesar_correo: aislamiento por documento")

motor = create_engine("sqlite://")
Base.metadata.create_all(motor)
Sesion = sessionmaker(bind=motor)
db = Sesion()

for nombre, descripcion in [
    ("Pendiente de factura", "x"), ("Pendiente de CP", "x"),
    ("Pendiente de revisión", "x"), ("Requiere captura manual", "x"),
    ("Cancelada", "x"), ("Revisada", "x"), ("Histórico", "x"),
    ("Pendiente de verificación", "x"),
]:
    db.add(Estados(nombre_estado=nombre, descripcion_estado=descripcion))
usuario_sistema = Usuarios(
    nombre="Sistema Automatico", correo="sistema@monsort.test",
    password_hash="x", rol="sistema",
)
db.add(usuario_sistema)
db.commit()

fs.settings.RFC_EMPRESA = RFC_EMPRESA

# Correo mezclado: factura buena + CP bueno + CP sin timbre (revienta) +
# otro CP bueno despues del que revienta.
resumen = fs.procesar_correo(
    {
        "f1.xml": xml_factura(UUID_F1, "100").encode(),
        "cp_ok.xml": xml_cp_pagos20("CP-OK", "CP100", UUID_F1).encode(),
        "cp_roto.xml": xml_cp_sin_timbre("CP101").encode(),
        "cp_ok2.xml": xml_cp_pagos10("CP-OK2", "CP102", UUID_F1).encode(),
    },
    asunto="Factura y complementos",
    mensaje_id="MSG-MEZCLA",
    db=db,
    usuario_sistema=usuario_sistema,
)
db.commit()

afirmar(resumen["facturas"] == 1, f"guardo la factura pese al CP roto ({resumen})")
afirmar(resumen["complementos"] == 2, f"guardo los 2 CPs buenos ({resumen})")
afirmar(resumen["errores"] == 1, f"conto 1 error, el CP sin timbre ({resumen})")

afirmar(db.query(Facturas).count() == 1, "la factura quedo en la base")
afirmar(db.query(ComplementosPago).count() == 2, "los 2 CPs quedaron en la base")
afirmar(db.query(CPDocumentosRelacionados).count() == 2,
        "los DoctoRelacionado quedaron en la base")

texto = fs.describir_resumen(resumen)
afirmar(len(texto) <= fs.LARGO_TIPO_CORREO and "errores:1" in texto,
        f"describir_resumen cabe en la columna y reporta errores: '{texto}'")

# El multi-documento que la base prohibia: dos facturas en un mismo correo.
resumen2 = fs.procesar_correo(
    {
        "a.xml": xml_factura(UUID_F2, "200").encode(),
        "b.xml": xml_factura("33333333-3333-3333-3333-333333333333", "201").encode(),
    },
    asunto="Dos facturas",
    mensaje_id="MSG-DOS-FACTURAS",
    db=db,
    usuario_sistema=usuario_sistema,
)
db.commit()
afirmar(resumen2["facturas"] == 2 and resumen2["errores"] == 0,
        f"dos facturas con el MISMO message_id ya entran ({resumen2})")

# Reprocesar el mismo correo no debe duplicar nada.
antes = db.query(Facturas).count(), db.query(ComplementosPago).count()
fs.procesar_correo(
    {
        "f1.xml": xml_factura(UUID_F1, "100").encode(),
        "cp_ok.xml": xml_cp_pagos20("CP-OK", "CP100", UUID_F1).encode(),
    },
    asunto="Reintento", mensaje_id="MSG-MEZCLA", db=db,
    usuario_sistema=usuario_sistema,
)
db.commit()
afirmar((db.query(Facturas).count(), db.query(ComplementosPago).count()) == antes,
        "reprocesar el mismo correo es idempotente, no duplica")


# ─────────────────────────────── 4. reconciliar

print("\n[4] reconciliar")

fs.reconciliar(db)
vinculados = db.query(CPDocumentosRelacionados).filter(
    CPDocumentosRelacionados.id_factura.isnot(None)
).count()
afirmar(vinculados == 2, f"pego los 2 documentos de CP a su factura ({vinculados})")

factura = db.query(Facturas).filter(Facturas.folio_fiscal == UUID_F1).first()
afirmar(factura.fecha_liquidacion is not None,
        "el CP con saldo insoluto 0 marco fecha_liquidacion")

db.close()

print()
if fallos:
    print(f"{len(fallos)} PRUEBA(S) FALLIDA(S):")
    for f in fallos:
        print("  -", f)
    sys.exit(1)
print("Todas las pruebas pasaron.")
