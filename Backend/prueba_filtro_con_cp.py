"""
Prueba el filtro con_cp del query builder.

El bug del 27/09/2026: ?con_cp=false devolvia CERO facturas mientras el
resumen de la misma consulta decia que habia 186 sin CP. La causa es el NULL
de cp_documentos_relacionados.id_factura dentro de un NOT IN, que envenena
toda la condicion. Basta UN pago huerfano en la tabla para que el filtro
descarte el universo entero, y en produccion habia diez.

Por eso la prueba siembra un pago huerfano: sin el, el NOT IN roto tambien
pasa, y la prueba estaria mintiendo.

    python3 prueba_filtro_con_cp.py
"""
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
from app.modelos.cp_documento_relacionado import CPDocumentosRelacionados  # noqa: E402
from app.esquemas.factura import FiltrosFactura  # noqa: E402
from app.services.query_builder import (  # noqa: E402
    construir_query_facturas, calcular_resumen,
)

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
db.add_all([estado, Estados(nombre_estado="Cancelada", descripcion_estado="x")])
usuario_sistema = Usuarios(nombre="Sistema Automatico", correo="s@m.test",
                           password_hash="x", rol="sistema")
db.add(usuario_sistema)
db.commit()


def factura(folio, uuid):
    return Facturas(
        folio_fiscal=uuid, folio_interno=folio, rfc="XAXX010101000",
        cliente="CLIENTE DEMO", fecha=date(2026, 9, 1), total=1000,
        moneda="MXN", tipo_cambio=1, origen="gmail",
        id_usuario=usuario_sistema.id_usuario, id_estado=estado.id_estado,
    )


# Dos con CP, tres sin. Los numeros chicos son a proposito: el bug no depende
# del volumen.
CON_CP = ["A-1", "A-2"]
SIN_CP = ["B-1", "B-2", "B-3"]

for i, folio in enumerate(CON_CP + SIN_CP):
    db.add(factura(folio, f"{i:08d}-0000-0000-0000-000000000000"))
db.commit()

facturas = {f.folio_interno: f for f in db.query(Facturas).all()}

cp_vivo = ComplementosPago(uuid_cp="CP-VIVO", folio="CP1", message_id="M1",
                           fecha_pago=datetime(2026, 9, 20), monto=2000)
cp_cancelado = ComplementosPago(uuid_cp="CP-MUERTO", folio="CP2", message_id="M2",
                                fecha_pago=datetime(2026, 9, 21), monto=500,
                                cancelado=True)
db.add_all([cp_vivo, cp_cancelado])
db.commit()

db.add_all([
    # Los dos pagos vinculados de verdad.
    CPDocumentosRelacionados(id_complemento=cp_vivo.id, uuid_documento="U1",
                             id_factura=facturas["A-1"].id_factura),
    CPDocumentosRelacionados(id_complemento=cp_vivo.id, uuid_documento="U2",
                             id_factura=facturas["A-2"].id_factura),
    # EL VENENO: un pago que no encontro su factura. id_factura = NULL.
    CPDocumentosRelacionados(id_complemento=cp_vivo.id, uuid_documento="U-HUERFANO",
                             id_factura=None),
    # Un pago de un CP cancelado: la factura B-3 NO cuenta como "con CP".
    CPDocumentosRelacionados(id_complemento=cp_cancelado.id, uuid_documento="U3",
                             id_factura=facturas["B-3"].id_factura),
])
db.commit()


def folios(con_cp):
    filtros = FiltrosFactura(con_cp=con_cp)
    return sorted(
        f.folio_interno
        for f in construir_query_facturas(db, filtros).all()
    )


print("[1] Hay un pago huerfano en la base (si no, la prueba no prueba nada)")

huerfanos = db.query(CPDocumentosRelacionados).filter(
    CPDocumentosRelacionados.id_factura.is_(None)).count()
afirmar(huerfanos == 1, f"un id_factura NULL sembrado ({huerfanos})")


print("\n[2] El filtro")

afirmar(folios(None) == sorted(CON_CP + SIN_CP),
        f"sin filtro salen las 5 ({folios(None)})")
afirmar(folios(True) == sorted(CON_CP),
        f"con_cp=true trae las 2 con CP vivo ({folios(True)})")
afirmar(folios(False) == sorted(SIN_CP),
        f"con_cp=false trae las 3 sin CP — el bug daba [] ({folios(False)})")

afirmar("B-3" in folios(False),
        "una factura cuyo unico CP esta cancelado cuenta como SIN CP")


print("\n[3] El resumen cuadra con el filtro")

resumen = calcular_resumen(db, FiltrosFactura())
afirmar(resumen.total_facturas == 5, f"5 facturas ({resumen.total_facturas})")
afirmar(resumen.total_con_cp == 2, f"2 con CP ({resumen.total_con_cp})")
afirmar(resumen.total_sin_cp == 3, f"3 sin CP ({resumen.total_sin_cp})")

# Esta es la contradiccion que reporto el front: el resumen decia 186 y el
# listado traia 0. Los dos caminos tienen que dar el mismo numero.
resumen_sin = calcular_resumen(db, FiltrosFactura(con_cp=False))
afirmar(resumen_sin.total_facturas == len(folios(False)),
        f"el resumen de ?con_cp=false cuenta lo mismo que el listado "
        f"({resumen_sin.total_facturas} vs {len(folios(False))})")
afirmar(resumen_sin.total_con_cp == 0,
        f"y ninguna de esas tiene CP ({resumen_sin.total_con_cp})")

resumen_con = calcular_resumen(db, FiltrosFactura(con_cp=True))
afirmar(resumen_con.total_facturas == 2 and resumen_con.total_sin_cp == 0,
        f"?con_cp=true sigue bien ({resumen_con.total_facturas}, "
        f"{resumen_con.total_sin_cp})")


print("\n[4] Se combina con otros filtros")

filtros = FiltrosFactura(con_cp=False, fecha_desde=date(2026, 9, 1),
                         fecha_hasta=date(2026, 9, 30))
combinado = sorted(f.folio_interno
                   for f in construir_query_facturas(db, filtros).all())
afirmar(combinado == sorted(SIN_CP),
        f"con_cp=false + rango de fechas ({combinado})")

db.close()

print()
if fallos:
    print(f"{len(fallos)} PRUEBA(S) FALLIDA(S):")
    for f in fallos:
        print("  -", f)
    sys.exit(1)
print("Todas las pruebas del filtro con_cp pasaron.")
