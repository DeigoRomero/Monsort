"""
Prueba el reporte general en PDF.

Los tres reclamos del cliente el 29/09/2026:
  - "no salen todas las facturas de acuerdo al filtro de fechas"
  - "los titulos de las columnas no se miran, estan color negro"
  - "que salga el importe, el IVA y el total"

    python3 prueba_reporte_general.py
"""
import io
import os
import sys
from datetime import date
from decimal import Decimal

os.environ.setdefault("DATABASE_URL", "sqlite://")
os.environ.setdefault("SECRET_KEY", "x")
os.environ.setdefault("ALGORITMO", "HS256")
os.environ.setdefault("TOKEN_ACCESO_MIN_EXPIRACION", "60")
os.environ.setdefault("GMAIL_CLIENT_ID", "x")
os.environ.setdefault("GMAIL_CLIENT_SECRET", "x")
os.environ.setdefault("GMAIL_REFRESH_TOKEN", "x")

import pdfplumber  # noqa: E402
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
from app.modelos.orden_compra import OrdenesCompra  # noqa: E402
from app.esquemas.factura import FiltrosFactura  # noqa: E402
from app.services.reporte_service import generar_reporte_general, _estilos, BLANCO  # noqa: E402

fallos = []


def afirmar(condicion, mensaje):
    print(("  ok   " if condicion else "  FALLA ") + mensaje)
    if not condicion:
        fallos.append(mensaje)


def texto_pdf(datos: bytes) -> str:
    with pdfplumber.open(io.BytesIO(datos)) as pdf:
        return "\n".join(p.extract_text() or "" for p in pdf.pages)


def plano(texto: str) -> str:
    """
    Sin espacios ni saltos: las columnas son angostas y el wordWrap parte las
    palabras donde sea, asi que "FAC1" puede salir como "A-COMPLET\nA".
    Para preguntar "aparece este folio" eso es ruido.
    """
    return "".join(texto.split())


motor = create_engine("sqlite://", connect_args={"check_same_thread": False},
                      poolclass=StaticPool)
Base.metadata.create_all(motor)
db = sessionmaker(bind=motor)()

estado = Estados(nombre_estado="Pendiente de CP", descripcion_estado="x")
db.add_all([estado, Estados(nombre_estado="Cancelada", descripcion_estado="x")])
u = Usuarios(nombre="Sistema Automatico", correo="s@m.test",
             password_hash="x", rol="sistema")
db.add(u)
db.commit()

oc = OrdenesCompra(numero_oc="OC-1", hash_archivo="h1", message_id="M-OC")
db.add(oc)
db.commit()


def factura(folio, uuid, origen, id_oc=None, dia=15):
    return Facturas(
        folio_fiscal=uuid, folio_interno=folio, rfc="XAXX010101000",
        cliente="CLIENTE DEMO", fecha=date(2026, 9, dia),
        subtotal=Decimal("1000.00"), iva=Decimal("80.00"),
        total=Decimal("1080.00"), moneda="MXN", tipo_cambio=1, origen=origen,
        id_usuario=u.id_usuario, id_estado=estado.id_estado,
        id_orden_compra=id_oc,
    )


# FAC1 tiene OC y CP. FAC2 no tiene nada. FAC3 viene del Excel. FAC9 queda
# fuera del rango de fechas. Folios cortos a proposito: con la columna de
# 2.1 cm, uno largo se parte en dos renglones y el texto extraido del PDF
# deja de ser contiguo.
completa = factura("FAC1", "11111111-0000-0000-0000-000000000001", "gmail", oc.id)
suelta = factura("FAC2", "22222222-0000-0000-0000-000000000002", "gmail")
historica = factura("FAC3", "33333333-0000-0000-0000-000000000003", "excel")
fuera = factura("FAC9", "44444444-0000-0000-0000-000000000004",
                "gmail", dia=2)
db.add_all([completa, suelta, historica, fuera])
db.commit()

cp = ComplementosPago(uuid_cp="CP-1", folio="CP1", message_id="M1", monto=1080)
db.add(cp)
db.commit()
db.add(CPDocumentosRelacionados(id_complemento=cp.id, uuid_documento=completa.folio_fiscal,
                                id_factura=completa.id_factura, imp_pagado=1080,
                                imp_saldo_insoluto=0))
db.commit()


print("[1] El filtro de fechas manda")

pdf = generar_reporte_general(
    db, FiltrosFactura(fecha_desde=date(2026, 9, 10), fecha_hasta=date(2026, 9, 30))
)
texto = texto_pdf(pdf)

afirmar("FAC1" in plano(texto), "sale la que tiene OC y CP")
afirmar("FAC2" in plano(texto),
        "y TAMBIEN la que no tiene ninguno — esto es lo que fallaba")
afirmar("FAC3" in plano(texto),
        "y la importada del Excel, que quedaba fuera por incluir_historico")
afirmar("FAC9" not in plano(texto),
        "la de fuera del rango no sale: el filtro de fechas si se respeta")
afirmar("(3facturas)" in plano(texto), "el total dice 3 facturas")


print("\n[2] solo_ciclo_completo recupera el comportamiento viejo")

pdf = generar_reporte_general(
    db, FiltrosFactura(fecha_desde=date(2026, 9, 10), fecha_hasta=date(2026, 9, 30)),
    solo_ciclo_completo=True,
)
texto = texto_pdf(pdf)
afirmar("FAC1" in plano(texto) and "FAC2" not in plano(texto),
        "con la casilla prendida, solo la de ciclo completo")


print("\n[3] Importe, IVA y Total, con el vocabulario del cliente")

pdf = generar_reporte_general(db, FiltrosFactura())
texto = texto_pdf(pdf)

afirmar("Importe" in texto, "la columna se llama Importe, no Subtotal")
afirmar("Subtotal" not in texto, "y ya no aparece la palabra Subtotal")
afirmar("IVA" in texto and "Total" in texto, "IVA y Total presentes")
afirmar("TotalMXN" in plano(texto), "y Total MXN para la conversion")

encabezados = ["Importe", "IVA", "Total"]
posiciones = [plano(texto).index(e) for e in encabezados]
afirmar(posiciones == sorted(posiciones),
        f"en ese orden: Importe, IVA, Total ({posiciones})")

afirmar("$1,000.00" in plano(texto) and "$80.00" in plano(texto)
        and "$1,080.00" in plano(texto),
        "los tres valores de la factura aparecen")


print("\n[4] Los titulos se ven")

estilos = _estilos()
afirmar(estilos["encabezado_tabla"].textColor == BLANCO,
        "el estilo del encabezado es blanco (el TEXTCOLOR del TableStyle no "
        "entra a un Paragraph, por eso salian negros)")
afirmar(estilos["celda_total"].textColor == BLANCO,
        "la fila de totales tambien, que va sobre azul")
afirmar(estilos["celda_bold"].textColor != BLANCO,
        "y el estilo de celda normal sigue siendo oscuro, para el resto")


print("\n[5] Sin resultados no miente sobre el motivo")

pdf = generar_reporte_general(
    db, FiltrosFactura(fecha_desde=date(2020, 1, 1), fecha_hasta=date(2020, 1, 2))
)
texto = texto_pdf(pdf)
afirmar("Noseencontraronfacturasparalosfiltros" in plano(texto),
        f"dice que no hay para el filtro, sin hablar de ciclo completo")

pdf = generar_reporte_general(
    db, FiltrosFactura(fecha_desde=date(2020, 1, 1), fecha_hasta=date(2020, 1, 2)),
    solo_ciclo_completo=True,
)
afirmar("ciclocompleto" in plano(texto_pdf(pdf)),
        "y con la casilla prendida si lo explica")

db.close()

print()
if fallos:
    print(f"{len(fallos)} PRUEBA(S) FALLIDA(S):")
    for f in fallos:
        print("  -", f)
    sys.exit(1)
print("Todas las pruebas del reporte general pasaron.")
