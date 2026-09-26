"""
Prueba el router /complementos contra una SQLite en memoria con datos sembrados.

Verifica lo que no se ve leyendo el codigo: que el orden de las rutas resuelva
(/resumen no se parsea como int), que los EXISTS correlacionados generen SQL
valido, y que los filtros devuelvan lo que dicen.

    python3 prueba_router_complementos.py
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
from app.modelos.usuario import Usuarios
from app.modelos.estados import Estados
from app.modelos.factura import Facturas
from app.modelos.complemento_pago import ComplementosPago
from app.modelos.cp_documento_relacionado import CPDocumentosRelacionados
from app.modelos.correo_procesado import CorreosProcesados, CorreosFallidos
from app.api.rutas.complementos import router

fallos = []


def afirmar(condicion, mensaje):
    print(("  ok   " if condicion else "  FALLA ") + mensaje)
    if not condicion:
        fallos.append(mensaje)


# ─────────────────────────────── Datos sembrados

# StaticPool + check_same_thread: TestClient corre el endpoint en un hilo del
# threadpool, y una SQLite en memoria con el pool por defecto le daria una
# conexion nueva (o sea, otra base vacia) a ese hilo.
motor = create_engine(
    "sqlite://",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
Base.metadata.create_all(motor)
Sesion = sessionmaker(bind=motor)
db = Sesion()

estado_pendiente = Estados(nombre_estado="Pendiente de CP", descripcion_estado="x")
estado_cancelada = Estados(nombre_estado="Cancelada", descripcion_estado="x")
db.add_all([estado_pendiente, estado_cancelada])
# reconciliar() exige que existan todos estos nombres
for nombre in ("Requiere captura manual", "Pendiente de revision",
               "Pendiente de revisi\u00f3n", "Pendiente de factura",
               "Revisada", "Hist\u00f3rico"):
    db.add(Estados(nombre_estado=nombre, descripcion_estado="x"))
usuario_sistema = Usuarios(nombre="Sistema Automatico", correo="s@m.test",
                           password_hash="x", rol="sistema")
db.add(usuario_sistema)
db.commit()

UUID_VIVA = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
UUID_FANTASMA = "ffffffff-ffff-ffff-ffff-ffffffffffff"
UUID_MUERTA = "dddddddd-dddd-dddd-dddd-dddddddddddd"

factura_viva = Facturas(
    folio_fiscal=UUID_VIVA, folio_interno="A100", rfc="XAXX010101000",
    cliente="CLIENTE DEMO", fecha=datetime(2026, 9, 1).date(), total=1160,
    moneda="MXN", id_usuario=usuario_sistema.id_usuario,
    id_estado=estado_pendiente.id_estado, message_id="MSG-1",
)
factura_muerta = Facturas(
    folio_fiscal=UUID_MUERTA, folio_interno="A101", rfc="XAXX010101000",
    cliente="CLIENTE DEMO", fecha=datetime(2026, 9, 2).date(), total=500,
    moneda="MXN", id_usuario=usuario_sistema.id_usuario,
    id_estado=estado_cancelada.id_estado, message_id="MSG-1",
)
db.add_all([factura_viva, factura_muerta])
db.commit()

# CP 1: vinculado y completo
cp_ok = ComplementosPago(
    uuid_cp="CP-OK", folio="CP576", fecha_pago=datetime(2026, 9, 22),
    moneda="MXN", tipo_cambio=1, monto=1160, forma_pago="03",
    archivo_xml=b"<xml/>", archivo_pdf=b"%PDF-1.4 fake",
    hash_archivo="h1", message_id="MSG-1",
)
# CP 2: huerfano (UUID de factura que no existe) y sin PDF
cp_huerfano = ComplementosPago(
    uuid_cp="CP-HUERFANO", folio="CP577", fecha_pago=datetime(2026, 9, 23),
    moneda="MXN", tipo_cambio=1, monto=800, forma_pago="03",
    archivo_xml=b"<xml/>", archivo_pdf=None, message_id="MSG-2",
)
# CP 3: sin fecha de pago (el caso que fecha_pago NOT NULL prohibia)
cp_sin_fecha = ComplementosPago(
    uuid_cp="CP-SIN-FECHA", folio="CP578", fecha_pago=None,
    moneda="MXN", tipo_cambio=1, monto=None, forma_pago=None,
    archivo_xml=b"<xml/>", message_id="MSG-3",
)
# CP 4: cancelado, no debe salir por defecto
cp_cancelado = ComplementosPago(
    uuid_cp="CP-CANCELADO", folio="CP579", fecha_pago=datetime(2026, 9, 10),
    moneda="MXN", tipo_cambio=1, monto=99, message_id="MSG-4", cancelado=True,
)
# CP 5: apunta a una factura que existe pero esta Cancelada (estado terminal)
cp_terminal = ComplementosPago(
    uuid_cp="CP-TERMINAL", folio="CP580", fecha_pago=datetime(2026, 9, 24),
    moneda="MXN", tipo_cambio=1, monto=500, message_id="MSG-5",
)
db.add_all([cp_ok, cp_huerfano, cp_sin_fecha, cp_cancelado, cp_terminal])
db.commit()

db.add_all([
    CPDocumentosRelacionados(id_complemento=cp_ok.id, uuid_documento=UUID_VIVA,
                             num_parcialidad=1, imp_pagado=1160,
                             imp_saldo_insoluto=0,
                             id_factura=factura_viva.id_factura),
    CPDocumentosRelacionados(id_complemento=cp_huerfano.id,
                             uuid_documento=UUID_FANTASMA, num_parcialidad=1,
                             imp_pagado=800, imp_saldo_insoluto=0,
                             id_factura=None),
    CPDocumentosRelacionados(id_complemento=cp_terminal.id,
                             uuid_documento=UUID_MUERTA, num_parcialidad=1,
                             imp_pagado=500, imp_saldo_insoluto=0,
                             id_factura=None),
])
db.add_all([
    CorreosProcesados(message_id="MSG-1", tipo_correo="facturas:2+complementos:1",
                      fecha_procesado=datetime(2026, 9, 22, 10)),
    CorreosProcesados(message_id="MSG-9", tipo_correo="desconocido",
                      fecha_procesado=datetime(2026, 9, 25, 10)),
    CorreosFallidos(message_id="MSG-CAIDO-1", error="<HttpError 403 rate limit>",
                    fecha_fallo=datetime(2026, 9, 18, 19, 5), resuelto=0, intentos=1),
    CorreosFallidos(message_id="MSG-CAIDO-2", error="<HttpError 404 not found>",
                    fecha_fallo=datetime(2026, 9, 24, 8, 9), resuelto=0, intentos=5),
    CorreosFallidos(message_id="MSG-RESCATADO", error="viejo",
                    fecha_fallo=datetime(2026, 9, 11, 13, 55), resuelto=1, intentos=2),
])
db.commit()


# ─────────────────────────────── App de prueba

app = FastAPI()
app.include_router(router, prefix="/complementos")
app.dependency_overrides[get_db] = lambda: db
cliente_http = TestClient(app)


print("\n[1] Listado y filtros")

r = cliente_http.get("/complementos/")
afirmar(r.status_code == 200, f"GET /complementos/ responde 200 ({r.status_code})")
cuerpo = r.json()
uuids = [c["uuid_cp"] for c in cuerpo["complementos"]]
afirmar("CP-CANCELADO" not in uuids, "el CP cancelado se excluye por defecto")
afirmar(len(uuids) == 4, f"devuelve los 4 CPs vivos ({uuids})")
afirmar(uuids[-1] == "CP-SIN-FECHA",
        f"el CP sin fecha de pago va al final, no encabeza la tabla ({uuids})")

resumen = cuerpo["resumen"]
afirmar(resumen["total_complementos"] == 4, "resumen: cuenta 4")
afirmar(resumen["sin_pdf"] == 3, f"resumen: 3 sin PDF ({resumen['sin_pdf']})")
afirmar(resumen["sin_fecha_pago"] == 1, "resumen: 1 sin fecha de pago")
afirmar(resumen["con_huerfanos"] == 2,
        f"resumen: 2 con documentos huerfanos ({resumen['con_huerfanos']})")
afirmar(resumen["totalmente_vinculados"] == 1,
        f"resumen: 1 totalmente vinculado ({resumen['totalmente_vinculados']})")

fila_ok = next(c for c in cuerpo["complementos"] if c["uuid_cp"] == "CP-OK")
afirmar(fila_ok["facturas"] == ["A100"], "la fila trae el folio de su factura")
afirmar(fila_ok["requiere_atencion"] is False, "el CP completo no pide atencion")

fila_h = next(c for c in cuerpo["complementos"] if c["uuid_cp"] == "CP-HUERFANO")
afirmar(fila_h["requiere_atencion"] is True and "sin factura" in fila_h["motivo_atencion"],
        f"el CP huerfano pide atencion: {fila_h['motivo_atencion']!r}")

r = cliente_http.get("/complementos/", params={"vinculado": "true"})
afirmar([c["uuid_cp"] for c in r.json()["complementos"]] == ["CP-OK"],
        "filtro vinculado=true deja solo el CP pegado")

r = cliente_http.get("/complementos/", params={"vinculado": "false"})
afirmar(sorted(c["uuid_cp"] for c in r.json()["complementos"]) ==
        ["CP-HUERFANO", "CP-TERMINAL"],
        "filtro vinculado=false deja los que tienen huerfanos")

r = cliente_http.get("/complementos/", params={"solo_atencion": "true"})
afirmar(len(r.json()["complementos"]) == 3,
        f"solo_atencion deja 3 ({[c['uuid_cp'] for c in r.json()['complementos']]})")

r = cliente_http.get("/complementos/", params={"incluir_cancelados": "true"})
afirmar(len(r.json()["complementos"]) == 5, "incluir_cancelados suma el quinto")

r = cliente_http.get("/complementos/", params={"q": "CP577"})
afirmar([c["uuid_cp"] for c in r.json()["complementos"]] == ["CP-HUERFANO"],
        "busqueda por folio del CP")

r = cliente_http.get("/complementos/", params={"q": UUID_VIVA})
afirmar([c["uuid_cp"] for c in r.json()["complementos"]] == ["CP-OK"],
        "busqueda por UUID de la factura referida")

r = cliente_http.get("/complementos/", params={"fecha_desde": "2026-09-23"})
afirmar(sorted(c["uuid_cp"] for c in r.json()["complementos"]) ==
        ["CP-HUERFANO", "CP-TERMINAL"], "filtro por fecha_desde")


print("\n[2] Rutas estaticas antes de /{id_cp}")

r = cliente_http.get("/complementos/resumen")
afirmar(r.status_code == 200, f"/resumen no se parsea como int ({r.status_code})")

r = cliente_http.get("/complementos/huerfanos")
afirmar(r.status_code == 200, f"/huerfanos responde 200 ({r.status_code})")
huerfanos = r.json()
afirmar(len(huerfanos) == 2, f"2 documentos huerfanos ({len(huerfanos)})")

por_uuid = {h["uuid_cp"]: h for h in huerfanos}
afirmar(por_uuid["CP-HUERFANO"]["factura_existe"] is False
        and "no está en la base" in por_uuid["CP-HUERFANO"]["motivo"],
        f"distingue la factura inexistente: {por_uuid['CP-HUERFANO']['motivo']!r}")
afirmar(por_uuid["CP-TERMINAL"]["factura_existe"] is True
        and "Cancelada" in por_uuid["CP-TERMINAL"]["motivo"],
        f"distingue la factura en estado terminal: {por_uuid['CP-TERMINAL']['motivo']!r}")

r = cliente_http.get("/complementos/diagnostico")
afirmar(r.status_code == 200, f"/diagnostico responde 200 ({r.status_code})")
d = r.json()
afirmar(d["complementos_guardados"] == 5, f"diagnostico cuenta los 5 CPs ({d['complementos_guardados']})")
afirmar(d["documentos_cp_huerfanos"] == 2, "diagnostico cuenta 2 documentos huerfanos")
afirmar(d["correos_fallidos_pendientes"] == 2, "diagnostico cuenta 2 correos caidos")
afirmar(d["correos_fallidos_reintentables"] == 1,
        "el que ya agoto los 5 intentos no cuenta como reintentable")
afirmar(d["correos_fallidos_resueltos"] == 1, "diagnostico cuenta 1 rescatado")
afirmar(len(d["errores_frecuentes"]) == 2, "agrupa los errores frecuentes")
afirmar(any(t["tipo_correo"] == "desconocido" for t in d["correos_por_tipo"]),
        "reporta el desglose por tipo de correo")

r = cliente_http.get("/complementos/correos-fallidos")
afirmar(r.status_code == 200 and len(r.json()) == 2, "/correos-fallidos lista los pendientes")
reintentables = {f["message_id"]: f["reintentable"] for f in r.json()}
afirmar(reintentables == {"MSG-CAIDO-1": True, "MSG-CAIDO-2": False},
        f"marca cual es reintentable ({reintentables})")


print("\n[3] Detalle y archivos")

r = cliente_http.get(f"/complementos/{cp_ok.id}")
afirmar(r.status_code == 200, f"detalle responde 200 ({r.status_code})")
det = r.json()
afirmar(det["documentos"][0]["factura"]["folio_interno"] == "A100",
        "el detalle trae la factura vinculada")
afirmar(det["documentos"][0]["liquida"] is True,
        "marca que el documento liquida (saldo insoluto 0)")
afirmar(det["tiene_pdf"] is True and det["tiene_xml"] is True, "banderas de archivos")

r = cliente_http.get(f"/complementos/{cp_huerfano.id}")
afirmar(r.json()["documentos"][0]["factura"] is None,
        "el detalle del huerfano no inventa factura")

r = cliente_http.get(f"/complementos/{cp_ok.id}/pdf")
afirmar(r.status_code == 200 and r.headers["content-type"] == "application/pdf",
        f"descarga el PDF del CP ({r.status_code})")
afirmar("CP576.pdf" in r.headers.get("content-disposition", ""),
        "el PDF sale nombrado con el folio del CP")

r = cliente_http.get(f"/complementos/{cp_ok.id}/xml")
afirmar(r.status_code == 200, "descarga el XML del CP")

r = cliente_http.get(f"/complementos/{cp_huerfano.id}/pdf")
afirmar(r.status_code == 404, f"404 cuando no hay PDF ({r.status_code})")

r = cliente_http.get("/complementos/999999")
afirmar(r.status_code == 404, "404 para un id que no existe")


print("\n[4] Reconciliar")

r = cliente_http.post("/complementos/reconciliar")
afirmar(r.status_code == 200, f"/reconciliar responde 200 ({r.status_code})")
cuerpo = r.json()
afirmar(cuerpo["documentos_cp_huerfanos"] == 2,
        f"el huerfano de factura inexistente y el de estado terminal siguen sin pegarse "
        f"({cuerpo})")

db.close()

print()
if fallos:
    print(f"{len(fallos)} PRUEBA(S) FALLIDA(S):")
    for f in fallos:
        print("  -", f)
    sys.exit(1)
print("Todas las pruebas del router pasaron.")
