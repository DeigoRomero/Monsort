"""
Pruebas de la metadata de emitidas y la reconciliacion contra el SAT.

Cubre lo que no se ve leyendo: que el parser sirva para las dos direcciones sin
romper recibidas, que el UPSERT respete los hechos de emision y refresque el
estatus, y que la cobertura por mes distinga "no falta nada" de "no he
consultado ese mes".

    python3 prueba_metadata_sat.py
"""
import io
import os
import sys
import zipfile
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
    correo_procesado, cliente, solicitud_sat, facturas_recibidas,
    metadata_emitida,
)
from app.modelos.usuario import Usuarios
from app.modelos.estados import Estados
from app.modelos.factura import Facturas
from app.modelos.metadata_emitida import MetadataEmitidas
from app.services import parser_metadata as pm

RFC_MONSORT = "MSF140227BF7"
fallos = []


def afirmar(condicion, mensaje):
    print(("  ok   " if condicion else "  FALLA ") + mensaje)
    if not condicion:
        fallos.append(mensaje)


ENCABEZADO = (
    "Uuid~RfcEmisor~NombreEmisor~RfcReceptor~NombreReceptor~RfcPac~"
    "FechaEmision~FechaCertificacionSat~Monto~EfectoComprobante~Estatus~"
    "FechaCancelacion"
)


def fila_emitida(uuid, fecha, monto="1160.00", estatus="1",
                 rfc_receptor="XAXX010101000", cancelacion="",
                 rfc_emisor=RFC_MONSORT):
    return (
        f"{uuid}~{rfc_emisor}~GRUPO MONSORT~{rfc_receptor}~CLIENTE DEMO~"
        f"PPD101129EA3~{fecha}~{fecha}~{monto}~I~{estatus}~{cancelacion}"
    )


def zip_metadata(filas, nombre="metadata.txt", con_bom=False):
    texto = "\n".join([ENCABEZADO] + filas)
    datos = texto.encode("utf-8-sig" if con_bom else "utf-8")
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as z:
        z.writestr(nombre, datos)
    return buffer.getvalue()


# ─────────────────────────────── 1. El parser en las dos direcciones

print("\n[1] parsear_metadata con rol")

UUID_A = "AAAAAAAA-1111-2222-3333-444444444444"
UUID_B = "BBBBBBBB-1111-2222-3333-444444444444"

paquete = zip_metadata([
    fila_emitida(UUID_A, "2026-07-15T10:00:00"),
    fila_emitida(UUID_B, "2026-08-03T11:30:00", monto="500.50"),
])

validas, rechazadas = pm.parsear_metadata(
    paquete, rol=pm.ROL_EMISOR, rfc_propio=RFC_MONSORT
)
afirmar(len(validas) == 2 and not rechazadas,
        f"rol=emisor acepta las filas donde Monsort emite ({len(validas)} validas, "
        f"{len(rechazadas)} rechazadas)")
afirmar(validas[0]["fecha_emision"] == datetime(2026, 7, 15, 10, 0),
        "saca la FechaEmision, que es el dato que faltaba")
afirmar(str(validas[1]["monto_total"]) == "500.50", "el monto entra como Decimal")
afirmar(validas[0]["sat_estado"] == "Vigente", "traduce el estatus 1 a 'Vigente'")

# El mismo paquete con rol=receptor debe rechazarlo todo: Monsort no es el
# receptor de sus propias emitidas.
validas_r, rechazadas_r = pm.parsear_metadata(
    paquete, rol=pm.ROL_RECEPTOR, rfc_propio=RFC_MONSORT
)
afirmar(not validas_r and len(rechazadas_r) == 2,
        "rol=receptor rechaza las emitidas (no confunde direcciones)")
afirmar("Receptor inesperado" in rechazadas_r[0]["motivo"],
        f"y explica por que: {rechazadas_r[0]['motivo'][:60]!r}")

# Recibidas: no se rompio el comportamiento anterior
paquete_recibida = zip_metadata([
    fila_emitida("CCCCCCCC-1111-2222-3333-444444444444", "2026-08-10T09:00:00",
                 rfc_emisor="PPP111111PP1", rfc_receptor=RFC_MONSORT),
])
validas_rec, rechazadas_rec = pm.parsear_metadata(paquete_recibida)
afirmar(len(validas_rec) == 1 and not rechazadas_rec,
        "el default sigue siendo receptor: recibidas no se rompio")

# BOM y cancelada
paquete_bom = zip_metadata(
    [fila_emitida("DDDDDDDD-1111-2222-3333-444444444444", "2026-09-01T08:00:00",
                  estatus="0", cancelacion="2026-09-05T12:00:00")],
    con_bom=True,
)
validas_bom, _ = pm.parsear_metadata(paquete_bom, rol=pm.ROL_EMISOR,
                                     rfc_propio=RFC_MONSORT)
afirmar(len(validas_bom) == 1, "un TXT con BOM se parsea igual")
afirmar(validas_bom[0]["sat_estado"] == "Cancelado"
        and validas_bom[0]["fecha_cancelacion"] is not None,
        "la cancelada trae estado y fecha de cancelacion")

try:
    pm.parsear_metadata(paquete, rol="inventado")
    afirmar(False, "un rol invalido debe tronar")
except ValueError:
    afirmar(True, "un rol invalido truena fuerte en vez de fallar callado")


# ─────────────────────────────── 2. La ingesta persiste

print("\n[2] ingerir_metadata_emitidas")

motor = create_engine("sqlite://")
Base.metadata.create_all(motor)
db = sessionmaker(bind=motor)()

usuario_sistema = Usuarios(nombre="Sistema Automatico", correo="s@m.test",
                           password_hash="x", rol="sistema")
estado = Estados(nombre_estado="Requiere captura manual", descripcion_estado="x")
db.add_all([usuario_sistema, estado])
db.commit()

from app.services.ingesta_emitidas_service import (
    ingerir_metadata_emitidas, cobertura_por_mes, faltantes, buscar_uuid,
)

# SQLite no tiene ON CONFLICT de Postgres con la sintaxis del dialecto pg,
# asi que la ingesta se prueba contra Postgres si esta disponible; si no,
# se marca y se salta. El resto del archivo si corre en SQLite.
POSTGRES = os.environ.get("URL_PRUEBA")

if not POSTGRES:
    print("  ·  ingesta y reconciliacion: requieren Postgres "
          "(exporta URL_PRUEBA para probarlas)")
else:
    motor_pg = create_engine(POSTGRES)
    with motor_pg.begin() as conexion:
        from sqlalchemy import text
        conexion.execute(text("DROP SCHEMA public CASCADE; CREATE SCHEMA public;"))
    Base.metadata.create_all(motor_pg)
    db_pg = sessionmaker(bind=motor_pg)()

    db_pg.add(Usuarios(nombre="Sistema Automatico", correo="s@m.test",
                       password_hash="x", rol="sistema"))
    db_pg.add(Estados(nombre_estado="Requiere captura manual",
                      descripcion_estado="x"))
    db_pg.commit()

    nuevas, actualizadas, rechazos = ingerir_metadata_emitidas(db_pg, paquete)
    db_pg.commit()
    afirmar((nuevas, actualizadas) == (2, 0),
            f"primera ingesta: 2 nuevas, 0 actualizadas ({nuevas}, {actualizadas})")
    afirmar(rechazos == "", "sin rechazos")

    guardada = db_pg.query(MetadataEmitidas).filter(
        MetadataEmitidas.folio_fiscal == UUID_A
    ).first()
    afirmar(guardada is not None and guardada.fecha_emision == datetime(2026, 7, 15, 10, 0),
            "la fecha de emision quedo guardada (antes se tiraba)")

    # Idempotencia + refresco de estatus: el mismo UUID ahora cancelado
    paquete_2 = zip_metadata([
        fila_emitida(UUID_A, "2026-07-15T10:00:00", estatus="0",
                     cancelacion="2026-09-20T10:00:00"),
        fila_emitida(UUID_B, "2026-08-03T11:30:00", monto="500.50"),
    ])
    nuevas2, actualizadas2, _ = ingerir_metadata_emitidas(db_pg, paquete_2)
    db_pg.commit()
    afirmar((nuevas2, actualizadas2) == (0, 2),
            f"segunda ingesta: 0 nuevas, 2 actualizadas ({nuevas2}, {actualizadas2})")

    db_pg.expire_all()
    guardada = db_pg.query(MetadataEmitidas).filter(
        MetadataEmitidas.folio_fiscal == UUID_A
    ).first()
    afirmar(guardada.sat_estado == "Cancelado",
            "el estatus SI se refresca: la cancelacion nueva entro")
    afirmar(guardada.fecha_emision == datetime(2026, 7, 15, 10, 0),
            "pero la fecha de emision NO se piso (es hecho de emision)")

    # UUID en minusculas: debe normalizarse igual que en el resto
    paquete_min = zip_metadata([
        fila_emitida("eeeeeeee-1111-2222-3333-444444444444", "2026-09-05T10:00:00"),
    ])
    ingerir_metadata_emitidas(db_pg, paquete_min)
    db_pg.commit()
    afirmar(db_pg.query(MetadataEmitidas).filter(
        MetadataEmitidas.folio_fiscal == "EEEEEEEE-1111-2222-3333-444444444444"
    ).first() is not None,
        "el UUID en minusculas se guarda normalizado a mayusculas")

    # ─────────────────────── 3. Reconciliacion

    print("\n[3] Cobertura y faltantes")

    # Tenemos UUID_B en Facturas; UUID_A y EEEE no.
    db_pg.add(Facturas(
        folio_fiscal=UUID_B, folio_interno="A-200", rfc="XAXX010101000",
        cliente="CLIENTE DEMO", fecha=datetime(2026, 8, 3).date(), total=500.50,
        moneda="MXN", origen="gmail",
        id_usuario=db_pg.query(Usuarios).first().id_usuario,
        id_estado=db_pg.query(Estados).first().id_estado,
    ))
    db_pg.commit()

    cobertura = {c["mes"]: c for c in cobertura_por_mes(db_pg)}

    afirmar(cobertura["2026-08"]["sat_dice"] == 1
            and cobertura["2026-08"]["tenemos"] == 1
            and cobertura["2026-08"]["faltantes"] == 0,
            f"agosto cuadra: 1 y 1, 0 faltantes ({cobertura['2026-08']})")

    # Julio: UUID_A esta cancelado, y las canceladas se excluyen por defecto
    afirmar("2026-07" not in cobertura or cobertura["2026-07"]["sat_dice"] == 0,
            "una cancelada no cuenta como faltante")

    afirmar(cobertura["2026-09"]["faltantes"] == 1,
            f"septiembre reporta 1 faltante ({cobertura.get('2026-09')})")
    afirmar(cobertura["2026-09"]["metadata_consultada"] is True,
            "y marca que si hay metadata de ese mes")

    lista = faltantes(db_pg)
    afirmar(len(lista) == 1 and lista[0]["mes"] == "2026-09",
            f"faltantes() devuelve el comprobante con su mes ({lista})")

    # ─────────────────────── 4. Busqueda por UUID

    print("\n[4] buscar_uuid: la consulta que resuelve un pago huerfano")

    hallazgo = buscar_uuid(db_pg, "eeeeeeee-1111-2222-3333-444444444444")
    afirmar(hallazgo["en_facturas"] is False and hallazgo["en_metadata_sat"] is True,
            "el UUID no esta en Facturas pero si en la metadata del SAT")
    afirmar(hallazgo["mes_sugerido"] == "2026-09",
            f"dice de que mes pedir la solicitud cfdi ({hallazgo['mes_sugerido']})")
    afirmar("2026-09" in hallazgo["recomendacion"],
            f"y lo explica: {hallazgo['recomendacion'][:70]!r}")

    hallazgo_b = buscar_uuid(db_pg, UUID_B)
    afirmar(hallazgo_b["en_facturas"] is True
            and "no hace falta" in hallazgo_b["recomendacion"],
            "para una que si tenemos, dice que no hay que pedir nada")

    hallazgo_x = buscar_uuid(db_pg, "99999999-9999-9999-9999-999999999999")
    afirmar(hallazgo_x["en_facturas"] is False
            and hallazgo_x["en_metadata_sat"] is False
            and "metadata" in hallazgo_x["recomendacion"],
            "para una desconocida, manda a pedir metadata antes de gastar cfdi")

    db_pg.close()

db.close()

print()
if fallos:
    print(f"{len(fallos)} PRUEBA(S) FALLIDA(S):")
    for f in fallos:
        print("  -", f)
    sys.exit(1)
print("Todas las pruebas pasaron.")
