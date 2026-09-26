"""
Prueba la migracion f3b6c1d84e92 contra un Postgres real.

Lo que importa no es el ALTER TABLE, es el relleno: los CP que ya estan en
produccion tienen el XML guardado en archivo_xml, y de ahi salen los RFC sin
reprocesar un solo correo. Si el regex falla, las columnas quedan en NULL, todo
se clasifica como "desconocido" y el arreglo no sirve de nada.

Parte del esquema en e5a3c17b9d24 (sin las columnas nuevas), siembra CP con XML
real y corre el upgrade.

    URL_PRUEBA=postgresql+psycopg2://... python3 prueba_migracion_rfc.py

OJO: borra el esquema public de la base a la que apunte URL_PRUEBA. Nunca
apuntarla a MonsortDB.
"""
import os
import subprocess
import sys

URL_PRUEBA = os.environ.get(
    "URL_PRUEBA",
    "postgresql+psycopg2://postgres@/monsort_prueba?host=/tmp&port=5433",
)

os.environ["DATABASE_URL"] = URL_PRUEBA
for clave, valor in {
    "SECRET_KEY": "x", "ALGORITMO": "HS256",
    "TOKEN_ACCESO_MIN_EXPIRACION": "60", "GMAIL_CLIENT_ID": "x",
    "GMAIL_CLIENT_SECRET": "x", "GMAIL_REFRESH_TOKEN": "x",
}.items():
    os.environ.setdefault(clave, valor)

from sqlalchemy import create_engine, text  # noqa: E402

from app.BaseDeDatos import Base  # noqa: E402
from app.modelos import (  # noqa: F401,E402
    usuario, factura, estados, configuracion, conceptos,
    complemento_pago, orden_compra, cp_documento_relacionado,
    correo_procesado, cliente, solicitud_sat, facturas_recibidas,
    metadata_emitida,
)

PROPIO = "MSF140227BF7"
OTRO = "AAA010101AAA"

fallos = []


def afirmar(condicion, mensaje):
    print(("  ok   " if condicion else "  FALLA ") + mensaje)
    if not condicion:
        fallos.append(mensaje)


def alembic(*args):
    """El directorio alembic/ del repo tapa a la libreria, asi que -m no sirve."""
    return subprocess.run(
        [sys.executable, "-c", "from alembic.config import main; main()", *args],
        capture_output=True, text=True,
    )


def xml_cp(uuid_cp, rfc_emisor, rfc_receptor):
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<cfdi:Comprobante xmlns:cfdi="http://www.sat.gob.mx/cfd/4"
 xmlns:tfd="http://www.sat.gob.mx/TimbreFiscalDigital" Version="4.0"
 TipoDeComprobante="P">
 <cfdi:Emisor Rfc="{rfc_emisor}" Nombre="ACENTOS Ñ SA" RegimenFiscal="601"/>
 <cfdi:Receptor Rfc="{rfc_receptor}" Nombre="OTRA SA" UsoCFDI="CP01"/>
 <cfdi:Complemento><tfd:TimbreFiscalDigital UUID="{uuid_cp}"/></cfdi:Complemento>
</cfdi:Comprobante>""".encode("utf-8")


motor = create_engine(URL_PRUEBA)

print("[1] Esquema en e5a3c17b9d24, sin las columnas nuevas")

with motor.begin() as cx:
    cx.execute(text("DROP SCHEMA public CASCADE"))
    cx.execute(text("CREATE SCHEMA public"))

Base.metadata.create_all(motor)

with motor.begin() as cx:
    cx.execute(text('ALTER TABLE "ComplementosPago" DROP COLUMN rfc_emisor'))
    cx.execute(text('ALTER TABLE "ComplementosPago" DROP COLUMN rfc_receptor'))

resultado = alembic("stamp", "e5a3c17b9d24")
afirmar(resultado.returncode == 0, f"stamp e5a3c17b9d24 ({resultado.stderr[-200:]})")

print("\n[2] CP sembrados como los de produccion")

with motor.begin() as cx:
    filas = [
        # (uuid, folio, emisor, receptor, con_xml)
        ("11111111-1111-1111-1111-111111111111", "CP627", PROPIO, OTRO, True),
        ("22222222-2222-2222-2222-222222222222", "CP1883", PROPIO.lower(),
         OTRO.lower(), True),          # PAC que escribe en minusculas
        ("33333333-3333-3333-3333-333333333333", "156", OTRO, "BBB020202BB1",
         True),                        # el ajeno real
        ("44444444-4444-4444-4444-444444444444", "CP999", None, None, False),
    ]
    for uuid_cp, folio, emisor, receptor, con_xml in filas:
        cx.execute(
            text("""INSERT INTO "ComplementosPago"
                    (uuid_cp, folio, message_id, cancelado, archivo_xml)
                    VALUES (:u, :f, :m, false, :x)"""),
            {"u": uuid_cp, "f": folio, "m": f"MSG-{folio}",
             "x": xml_cp(uuid_cp, emisor, receptor) if con_xml else None},
        )

print("\n[3] upgrade head")

resultado = alembic("upgrade", "head")
afirmar(resultado.returncode == 0,
        f"upgrade corre sin error ({resultado.stderr[-300:]})")

with motor.connect() as cx:
    datos = {
        f[0]: (f[1], f[2])
        for f in cx.execute(text(
            'SELECT folio, rfc_emisor, rfc_receptor FROM "ComplementosPago"'
        )).fetchall()
    }

afirmar(datos["CP627"] == (PROPIO, OTRO),
        f"CP propio rellenado desde su XML ({datos.get('CP627')})")
afirmar(datos["CP1883"] == (PROPIO, OTRO),
        f"y en mayusculas aunque el XML venga en minusculas ({datos.get('CP1883')})")
afirmar(datos["156"] == (OTRO, "BBB020202BB1"),
        f"el ajeno queda identificado como ajeno ({datos.get('156')})")
afirmar(datos["CP999"] == (None, None),
        f"sin XML no se inventa nada: queda NULL ({datos.get('CP999')})")

with motor.connect() as cx:
    indices = [f[0] for f in cx.execute(text("""
        SELECT indexname FROM pg_indexes
        WHERE tablename = 'ComplementosPago'
    """)).fetchall()]
afirmar("ix_complementos_rfc_emisor" in indices,
        f"indice creado ({indices})")

print("\n[4] Es idempotente")

resultado = alembic("upgrade", "head")
afirmar(resultado.returncode == 0, "correrla de nuevo no hace nada y no falla")

print("\n[5] Baja y vuelve a subir")

resultado = alembic("downgrade", "e5a3c17b9d24")
afirmar(resultado.returncode == 0, f"downgrade ({resultado.stderr[-200:]})")

with motor.connect() as cx:
    columnas = [f[0] for f in cx.execute(text("""
        SELECT column_name FROM information_schema.columns
        WHERE table_name = 'ComplementosPago'
    """)).fetchall()]
afirmar("rfc_emisor" not in columnas and "rfc_receptor" not in columnas,
        "el downgrade quita las dos columnas")

resultado = alembic("upgrade", "head")
afirmar(resultado.returncode == 0, "y vuelve a subir")

print()
if fallos:
    print(f"{len(fallos)} PRUEBA(S) FALLIDA(S):")
    for f in fallos:
        print("  -", f)
    sys.exit(1)
print("Todas las pruebas de la migracion f3b6c1d84e92 pasaron.")
