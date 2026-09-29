"""
Prueba que el detalle de una factura traiga sus complementos de pago.

Lo pidio Alexander para la pantalla de facturas emitidas: poder ver a que CP
esta ligada una factura, con lo necesario para enlazar a su pantalla y para
saber si ese pago la liquido.

    python3 prueba_detalle_factura_cp.py
"""
import os
import sys
from datetime import date, datetime
from decimal import Decimal

os.environ.setdefault("DATABASE_URL", "sqlite://")
os.environ.setdefault("SECRET_KEY", "x")
os.environ.setdefault("ALGORITMO", "HS256")
os.environ.setdefault("TOKEN_ACCESO_MIN_EXPIRACION", "60")
os.environ.setdefault("GMAIL_CLIENT_ID", "x")
os.environ.setdefault("GMAIL_CLIENT_SECRET", "x")
os.environ.setdefault("GMAIL_REFRESH_TOKEN", "x")

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

from app.BaseDeDatos import Base, get_db  # noqa: E402
from app.modelos import (  # noqa: F401,E402
    usuario, factura, estados, configuracion, conceptos,
    complemento_pago, orden_compra, cp_documento_relacionado,
    correo_procesado, cliente, solicitud_sat,
)
from app.modelos.usuario import Usuarios  # noqa: E402
from app.modelos.estados import Estados  # noqa: E402
from app.modelos.factura import Facturas  # noqa: E402
from app.modelos.complemento_pago import ComplementosPago  # noqa: E402
from app.modelos.cp_documento_relacionado import CPDocumentosRelacionados  # noqa: E402
from app.api.rutas.facturas import router  # noqa: E402

fallos = []


def afirmar(condicion, mensaje):
    print(("  ok   " if condicion else "  FALLA ") + mensaje)
    if not condicion:
        fallos.append(mensaje)


motor = create_engine("sqlite://", connect_args={"check_same_thread": False},
                      poolclass=StaticPool)
Base.metadata.create_all(motor)
db = sessionmaker(bind=motor)()

estado = Estados(nombre_estado="Pendiente de CP", descripcion_estado="x")
db.add(estado)
usuario_sistema = Usuarios(nombre="Sistema Automatico", correo="s@m.test",
                           password_hash="x", rol="sistema")
db.add(usuario_sistema)
db.commit()

UUID_FACTURA = "AAAAAAAA-0000-0000-0000-000000000001"

f = Facturas(
    folio_fiscal=UUID_FACTURA, folio_interno="A-500", rfc="XAXX010101000",
    cliente="CLIENTE DEMO", fecha=date(2026, 9, 1), total=Decimal("3000.00"),
    moneda="MXN", tipo_cambio=1, origen="gmail",
    id_usuario=usuario_sistema.id_usuario, id_estado=estado.id_estado,
)
db.add(f)
db.commit()

# Dos pagos parciales y un CP cancelado que no debe aparecer.
cp1 = ComplementosPago(uuid_cp="CP-UUID-1", folio="CP100", message_id="M1",
                       fecha_pago=datetime(2026, 9, 10), monto=Decimal("1000.00"),
                       moneda="MXN", forma_pago="03")
cp2 = ComplementosPago(uuid_cp="CP-UUID-2", folio=None, message_id="M2",
                       fecha_pago=datetime(2026, 9, 20), monto=Decimal("5000.00"),
                       moneda="MXN", forma_pago="03")
cp_cancelado = ComplementosPago(uuid_cp="CP-UUID-3", folio="CP102", message_id="M3",
                                fecha_pago=datetime(2026, 9, 25), monto=Decimal("9.00"),
                                cancelado=True)
db.add_all([cp1, cp2, cp_cancelado])
db.commit()

db.add_all([
    CPDocumentosRelacionados(id_complemento=cp1.id, uuid_documento=UUID_FACTURA,
                             id_factura=f.id_factura, num_parcialidad=1,
                             imp_pagado=Decimal("1000.00"),
                             imp_saldo_insoluto=Decimal("2000.00")),
    CPDocumentosRelacionados(id_complemento=cp2.id, uuid_documento=UUID_FACTURA,
                             id_factura=f.id_factura, num_parcialidad=2,
                             imp_pagado=Decimal("2000.00"),
                             imp_saldo_insoluto=Decimal("0.00")),
    CPDocumentosRelacionados(id_complemento=cp_cancelado.id,
                             uuid_documento=UUID_FACTURA,
                             id_factura=f.id_factura, num_parcialidad=3,
                             imp_pagado=Decimal("9.00"),
                             imp_saldo_insoluto=Decimal("0.00")),
])
db.commit()

app = FastAPI()
app.dependency_overrides[get_db] = lambda: db
app.include_router(router, prefix="/facturas")
http = TestClient(app)

r = http.get(f"/facturas/{f.id_factura}")
afirmar(r.status_code == 200, f"el detalle responde 200 ({r.status_code})")
detalle = r.json()
cps = detalle["complementos"]


print("[1] Los CPs vienen en el detalle")

afirmar(len(cps) == 2,
        f"los dos pagos vivos, sin el del CP cancelado ({len(cps)})")
afirmar([c["num_parcialidad"] for c in cps] == [1, 2],
        f"en orden de parcialidad ({[c['num_parcialidad'] for c in cps]})")


print("\n[2] Cada uno trae lo que la pantalla necesita")

primero, segundo = cps

afirmar(primero["id"] == cp1.id,
        "el id es el del complemento, para enlazar a /complementos/{id}")
afirmar(primero["folio"] == "CP100", f"folio ({primero['folio']})")
afirmar(primero["uuid_cp"] == "CP-UUID-1",
        "el uuid_cp, que es lo que se muestra cuando no hay folio")
afirmar(segundo["folio"] is None and segundo["uuid_cp"] == "CP-UUID-2",
        "un CP sin folio trae folio null y su uuid: el front decide cual pinta")
afirmar(primero["fecha_pago"].startswith("2026-09-10"),
        f"fecha de pago ({primero['fecha_pago']})")
afirmar(primero["imp_pagado"] == "1000.00", f"importe pagado ({primero['imp_pagado']})")
afirmar(primero["imp_saldo_insoluto"] == "2000.00",
        f"saldo insoluto ({primero['imp_saldo_insoluto']})")


print("\n[3] La bandera liquida")

afirmar(primero["liquida"] is False,
        "el primer pago deja saldo: no liquida")
afirmar(segundo["liquida"] is True,
        "el segundo lo deja en cero: liquida")


print("\n[4] monto e imp_pagado son cosas distintas")

afirmar(segundo["monto"] == "5000.00" and segundo["imp_pagado"] == "2000.00",
        f"el CP cubre 5,000 en total y 2,000 de ESTA factura "
        f"({segundo['monto']} vs {segundo['imp_pagado']})")

db.close()

print()
if fallos:
    print(f"{len(fallos)} PRUEBA(S) FALLIDA(S):")
    for f in fallos:
        print("  -", f)
    sys.exit(1)
print("Todas las pruebas del detalle con CP pasaron.")
