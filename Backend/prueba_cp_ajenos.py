"""
Prueba el filtro de CP por RFC: propios adentro, ajenos afuera.

El caso real: el CP con folio 156 se guardo en produccion aunque ni el emisor
ni el receptor eran la empresa. Sus tres DoctoRelacionado apuntan a facturas de
un tercero, que nunca van a estar en Facturas, y aparecian en
/complementos/huerfanos como pendientes del cliente.

    python3 prueba_cp_ajenos.py
"""
import os
import sys
from datetime import datetime

os.environ.setdefault("DATABASE_URL", "sqlite://")
os.environ.setdefault("SECRET_KEY", "x")
os.environ.setdefault("ALGORITMO", "HS256")
os.environ.setdefault("TOKEN_ACCESO_MIN_EXPIRACION", "60")
os.environ.setdefault("GMAIL_CLIENT_ID", "x")
os.environ.setdefault("GMAIL_CLIENT_SECRET", "x")
os.environ.setdefault("GMAIL_REFRESH_TOKEN", "x")

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.BaseDeDatos import Base, get_db
from app.modelos import (  # noqa: F401
    usuario, factura, estados, configuracion, conceptos,
    complemento_pago, orden_compra, cp_documento_relacionado,
    correo_procesado, cliente, solicitud_sat,
)
from app.modelos.complemento_pago import ComplementosPago
from app.modelos.cp_documento_relacionado import CPDocumentosRelacionados
from app.api.rutas.complementos import (
    router, _direccion,
    DIRECCION_EMITIDO, DIRECCION_RECIBIDO, DIRECCION_AJENO,
    DIRECCION_DESCONOCIDA,
)
from app.services.factura_service import extraer_datos_cp, procesar_complemento_pago
from app.core.config import settings

PROPIO = settings.RFC_EMPRESA.upper()
OTRO = "AAA010101AAA"
TERCERO = "BBB020202BB1"

fallos = []


def afirmar(condicion, mensaje):
    print(("  ok   " if condicion else "  FALLA ") + mensaje)
    if not condicion:
        fallos.append(mensaje)


# ─────────────────────────────── XML de prueba

def cp_xml(uuid_cp, rfc_emisor, rfc_receptor, uuid_doc,
           namespace="http://www.sat.gob.mx/Pagos20", prefijo="pago20"):
    """CP minimo pero con la forma real: Emisor, Receptor, Pago y timbre."""
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<cfdi:Comprobante xmlns:cfdi="http://www.sat.gob.mx/cfd/4"
                  xmlns:{prefijo}="{namespace}"
                  xmlns:tfd="http://www.sat.gob.mx/TimbreFiscalDigital"
                  Version="4.0" Serie="CP" Folio="900"
                  Fecha="2026-09-22T10:00:00" TipoDeComprobante="P">
  <cfdi:Emisor Rfc="{rfc_emisor}" Nombre="EMISOR SA" RegimenFiscal="601"/>
  <cfdi:Receptor Rfc="{rfc_receptor}" Nombre="RECEPTOR SA"
                 UsoCFDI="CP01" DomicilioFiscalReceptor="22000"
                 RegimenFiscalReceptor="601"/>
  <cfdi:Complemento>
    <{prefijo}:Pagos Version="2.0">
      <{prefijo}:Pago FechaPago="2026-09-22T00:00:00" FormaDePagoP="03"
                      MonedaP="MXN" TipoCambioP="1" Monto="1160.00">
        <{prefijo}:DoctoRelacionado IdDocumento="{uuid_doc}"
                                    NumParcialidad="1" ImpPagado="1160.00"
                                    ImpSaldoInsoluto="0.00"/>
      </{prefijo}:Pago>
    </{prefijo}:Pagos>
    <tfd:TimbreFiscalDigital UUID="{uuid_cp}"/>
  </cfdi:Complemento>
</cfdi:Comprobante>""".encode("utf-8")


UUID_PROPIO = "11111111-1111-1111-1111-111111111111"
UUID_RECIBIDO = "22222222-2222-2222-2222-222222222222"
UUID_AJENO = "33333333-3333-3333-3333-333333333333"
UUID_SIN_RFC = "44444444-4444-4444-4444-444444444444"
UUID_DOC = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"


# ─────────────────────────────── Base de prueba

motor = create_engine("sqlite://", connect_args={"check_same_thread": False},
                      poolclass=StaticPool)
Base.metadata.create_all(motor)
Sesion = sessionmaker(bind=motor)
db = Sesion()


print("[1] extraer_datos_cp lee las partes del CFDI")

datos = extraer_datos_cp(cp_xml(UUID_PROPIO, PROPIO, OTRO, UUID_DOC))
afirmar(datos["rfc_emisor"] == PROPIO, f"emisor leido ({datos['rfc_emisor']})")
afirmar(datos["rfc_receptor"] == OTRO, f"receptor leido ({datos['rfc_receptor']})")

# Pagos10 (CFDI 3.3): otro namespace, mismos nombres de etiqueta.
viejo = extraer_datos_cp(cp_xml(
    UUID_PROPIO, PROPIO, OTRO, UUID_DOC,
    namespace="http://www.sat.gob.mx/Pagos", prefijo="pago10"))
afirmar(viejo["rfc_emisor"] == PROPIO,
        "tambien los lee en un CP viejo de Pagos10")

# Minusculas: hay PACs que escriben el RFC asi, igual que con IdDocumento.
minusculas = extraer_datos_cp(cp_xml(
    UUID_PROPIO, PROPIO.lower(), OTRO.lower(), UUID_DOC))
afirmar(minusculas["rfc_emisor"] == PROPIO,
        "normaliza el RFC a mayusculas (era la causa de los 23 huerfanos)")

sin_partes = extraer_datos_cp(b"""<?xml version="1.0"?>
<Comprobante xmlns:tfd="http://www.sat.gob.mx/TimbreFiscalDigital">
  <Complemento><tfd:TimbreFiscalDigital UUID="55555555-5555-5555-5555-555555555555"/>
  </Complemento></Comprobante>""")
afirmar(sin_partes["rfc_emisor"] is None and sin_partes["rfc_receptor"] is None,
        "un XML sin Emisor/Receptor devuelve None, no revienta")


print("\n[2] procesar_complemento_pago decide por RFC")

guardado = procesar_complemento_pago(
    cp_xml(UUID_PROPIO, PROPIO, OTRO, UUID_DOC), "MSG-PROPIO", db, [])
afirmar(guardado, "guarda el CP que emitio la empresa")

recibido = procesar_complemento_pago(
    cp_xml(UUID_RECIBIDO, OTRO, PROPIO, UUID_DOC), "MSG-RECIBIDO", db, [])
afirmar(recibido, "guarda el CP de un proveedor donde la empresa es receptor")

ajeno = procesar_complemento_pago(
    cp_xml(UUID_AJENO, OTRO, TERCERO, UUID_DOC), "MSG-AJENO", db, [])
afirmar(not ajeno, "NO guarda el CP donde ninguna parte es la empresa")

sin_rfc = procesar_complemento_pago(
    cp_xml(UUID_SIN_RFC, "", "", UUID_DOC), "MSG-SIN-RFC", db, [])
afirmar(sin_rfc,
        "guarda el CP sin RFC legibles: no saber no es razon para tirarlo")

db.commit()

guardados = {c.uuid_cp: c for c in db.query(ComplementosPago).all()}
afirmar(UUID_AJENO not in guardados,
        "el ajeno no quedo en la base")
afirmar(guardados[UUID_PROPIO].rfc_emisor == PROPIO,
        "el RFC del emisor se guardo en la columna")
afirmar(guardados[UUID_RECIBIDO].rfc_receptor == PROPIO,
        "el RFC del receptor se guardo en la columna")

docs_ajenos = db.query(CPDocumentosRelacionados).join(
    ComplementosPago,
    ComplementosPago.id == CPDocumentosRelacionados.id_complemento
).filter(ComplementosPago.uuid_cp == UUID_AJENO).count()
afirmar(docs_ajenos == 0,
        "tampoco quedaron sus DoctoRelacionado (eran 3 en produccion)")


print("\n[3] _direccion clasifica")

afirmar(_direccion(PROPIO, OTRO) == DIRECCION_EMITIDO, "emitido")
afirmar(_direccion(OTRO, PROPIO) == DIRECCION_RECIBIDO, "recibido")
afirmar(_direccion(OTRO, TERCERO) == DIRECCION_AJENO, "ajeno")
afirmar(_direccion(None, None) == DIRECCION_DESCONOCIDA, "desconocido sin RFC")
afirmar(_direccion(OTRO, None) == DIRECCION_DESCONOCIDA,
        "con un solo RFC conocido no se afirma que sea ajeno")


print("\n[4] /huerfanos deja de reportar lo que no es pendiente")

# Un ajeno ya guardado antes del arreglo, como los 3 de produccion.
cp_viejo_ajeno = ComplementosPago(
    uuid_cp="CP-AJENO-VIEJO", folio="156", fecha_pago=datetime(2026, 9, 4),
    moneda="MXN", monto=17553.53, message_id="MSG-VIEJO",
    rfc_emisor=OTRO, rfc_receptor=TERCERO,
)
db.add(cp_viejo_ajeno)
db.commit()
db.add(CPDocumentosRelacionados(
    id_complemento=cp_viejo_ajeno.id,
    uuid_documento="72149127-b60d-477b-b80d-1f1f4413ed85".upper(),
    num_parcialidad=1, imp_pagado=17553.53, id_factura=None,
))
db.commit()

app = FastAPI()
app.dependency_overrides[get_db] = lambda: db
app.include_router(router, prefix="/complementos")
http = TestClient(app)

r = http.get("/complementos/huerfanos")
afirmar(r.status_code == 200, f"/huerfanos responde 200 ({r.status_code})")
uuids = [h["uuid_cp"] for h in r.json()]
afirmar("CP-AJENO-VIEJO" not in uuids,
        "el CP ajeno ya guardado no se reporta como pendiente")
afirmar(UUID_SIN_RFC in uuids,
        "el CP sin RFC legibles SI se sigue reportando: no se oculta lo dudoso")

r = http.get("/complementos/huerfanos", params={"incluir_no_emitidos": "true"})
detalle = {h["uuid_cp"]: h for h in r.json()}
afirmar("CP-AJENO-VIEJO" in detalle,
        "con incluir_no_emitidos=true vuelve a salir, para poder revisarlo")
afirmar(detalle["CP-AJENO-VIEJO"]["direccion"] == DIRECCION_AJENO,
        "y viene marcado como ajeno")
afirmar("otra empresa" in detalle["CP-AJENO-VIEJO"]["motivo"],
        f"con un motivo que lo explica ({detalle['CP-AJENO-VIEJO']['motivo'][:60]})")

r = http.get("/complementos/", params={"direccion": "ajeno"})
afirmar(r.status_code == 200, f"filtro direccion=ajeno responde 200 ({r.status_code})")
cuerpo = r.json()
afirmar([c["uuid_cp"] for c in cuerpo["complementos"]] == ["CP-AJENO-VIEJO"],
        f"y devuelve solo el ajeno ({[c['uuid_cp'] for c in cuerpo['complementos']]})")
afirmar(cuerpo["complementos"][0]["requiere_atencion"] is True,
        "marcado para atencion: hay que borrarlo")

r = http.get("/complementos/", params={"direccion": "emitido"})
afirmar([c["uuid_cp"] for c in r.json()["complementos"]] == [UUID_PROPIO],
        "filtro direccion=emitido devuelve solo el propio")

r = http.get("/complementos/", params={"direccion": "cualquier-cosa"})
afirmar(r.status_code == 422, f"una direccion invalida da 422 ({r.status_code})")

r = http.get("/complementos/diagnostico")
diag = r.json()
afirmar(diag["complementos_ajenos"] == 1,
        f"el diagnostico cuenta 1 ajeno ({diag['complementos_ajenos']})")
afirmar(diag["complementos_recibidos"] == 1,
        f"y 1 recibido ({diag['complementos_recibidos']})")


print("\n[5] El relleno de la migracion lee el XML guardado")

# Por ruta y no por import: "alembic" resuelve a la libreria instalada, no al
# directorio del repo, asi que alembic.versions.* no existe como modulo.
import importlib.util  # noqa: E402

_ruta = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                     "alembic", "versions",
                     "f3b6c1d84e92_rfc_en_complementos.py")
_spec = importlib.util.spec_from_file_location("migracion_rfc", _ruta)
_migracion = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_migracion)
PATRON_EMISOR = _migracion.PATRON_EMISOR
PATRON_RECEPTOR = _migracion.PATRON_RECEPTOR
_rfc = _migracion._rfc

xml = cp_xml(UUID_PROPIO, PROPIO, OTRO, UUID_DOC)
afirmar(_rfc(PATRON_EMISOR.search(xml)) == PROPIO,
        "el regex saca el emisor del XML crudo")
afirmar(_rfc(PATRON_RECEPTOR.search(xml)) == OTRO,
        "y el receptor, sin confundirlo con el del emisor")

# Sin prefijo de namespace y con los atributos en otro orden.
suelto = b'<Emisor Nombre="X SA" Rfc="AAA010101AAA"/><Receptor Rfc="BBB020202BB1"/>'
afirmar(_rfc(PATRON_EMISOR.search(suelto)) == OTRO,
        "tambien sin prefijo cfdi: y con Rfc despues de Nombre")

afirmar(_rfc(PATRON_EMISOR.search(b"<Comprobante/>")) is None,
        "sin coincidencia devuelve None")
afirmar(_rfc(PATRON_EMISOR.search(b'<Emisor Rfc="ESTO-NO-CABE-EN-TRECE"/>')) is None,
        "un valor mas largo que un RFC se descarta en vez de reventar el UPDATE")

db.close()

print()
if fallos:
    print(f"{len(fallos)} PRUEBA(S) FALLIDA(S):")
    for f in fallos:
        print("  -", f)
    sys.exit(1)
print("Todas las pruebas de CP ajenos pasaron.")
