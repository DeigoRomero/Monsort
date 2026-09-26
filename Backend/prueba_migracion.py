"""
Prueba la migracion c4f1a9b27d30 contra un Postgres real, partiendo del estado
EXACTO de produccion al 26/09/2026: los UNIQUE de message_id vivos (bce9ea1ab852
nunca corrio su DDL), fecha_pago NOT NULL, tipo_correo VARCHAR(30) y sin la
columna intentos.

    python3 prueba_migracion.py            # necesita URL_PRUEBA abajo
"""
import os
import subprocess
import sys

URL_PRUEBA = os.environ.get(
    "URL_PRUEBA",
    "postgresql+psycopg2://postgres@/monsort_prueba?host=/tmp/pgrun&port=5433",
)

os.environ["DATABASE_URL"] = URL_PRUEBA
for clave, valor in {
    "SECRET_KEY": "x", "ALGORITMO": "HS256",
    "TOKEN_ACCESO_MIN_EXPIRACION": "60", "GMAIL_CLIENT_ID": "x",
    "GMAIL_CLIENT_SECRET": "x", "GMAIL_REFRESH_TOKEN": "x",
}.items():
    os.environ.setdefault(clave, valor)

from sqlalchemy import create_engine, text

from app.BaseDeDatos import Base
from app.modelos import (  # noqa: F401
    usuario, factura, estados, configuracion, conceptos,
    complemento_pago, orden_compra, cp_documento_relacionado,
    correo_procesado, cliente, solicitud_sat, facturas_recibidas,
)

fallos = []


def afirmar(condicion, mensaje):
    print(("  ok   " if condicion else "  FALLA ") + mensaje)
    if not condicion:
        fallos.append(mensaje)


motor = create_engine(URL_PRUEBA)


def uniques(conn, tabla, columna):
    return [f[0] for f in conn.execute(text("""
        SELECT con.conname FROM pg_constraint con
        JOIN pg_class rel ON rel.oid = con.conrelid
        WHERE rel.relname = :t AND con.contype = 'u'
          AND array_length(con.conkey, 1) = 1
          AND con.conkey[1] = (SELECT attnum FROM pg_attribute
                               WHERE attrelid = con.conrelid AND attname = :c
                                 AND attnum > 0 AND NOT attisdropped)
    """), {"t": tabla, "c": columna})]


def indice_existe(conn, nombre):
    return conn.execute(text(
        "SELECT 1 FROM pg_class WHERE relname = :n AND relkind = 'i'"
    ), {"n": nombre}).first() is not None


def columna(conn, tabla, col):
    return conn.execute(text("""
        SELECT is_nullable, character_maximum_length
        FROM information_schema.columns
        WHERE table_name = :t AND column_name = :c
    """), {"t": tabla, "c": col}).first()


# ─────────────────────────── 1. Reconstruir el estado de produccion

print("\n[1] Reconstruyendo el estado de produccion (pre-migracion)")

with motor.begin() as conn:
    conn.execute(text("DROP SCHEMA public CASCADE; CREATE SCHEMA public;"))

Base.metadata.create_all(motor)

with motor.begin() as conn:
    # Los modelos ya estan parchados, asi que hay que devolverlos al estado
    # que tiene el VPS hoy.
    conn.execute(text('DROP INDEX IF EXISTS ix_facturas_message_id'))
    conn.execute(text('DROP INDEX IF EXISTS ix_cp_message_id'))
    conn.execute(text('DROP INDEX IF EXISTS ix_oc_message_id'))
    conn.execute(text('DROP INDEX IF EXISTS ix_cp_hash_archivo'))

    conn.execute(text('ALTER TABLE "Facturas" ADD CONSTRAINT "Facturas_message_id_key" UNIQUE (message_id)'))
    conn.execute(text('ALTER TABLE "ComplementosPago" ADD CONSTRAINT "ComplementosPago_message_id_key" UNIQUE (message_id)'))
    conn.execute(text('ALTER TABLE "ComplementosPago" ADD CONSTRAINT "ComplementosPago_hash_archivo_key" UNIQUE (hash_archivo)'))
    conn.execute(text('ALTER TABLE ordenes_compra ADD CONSTRAINT ordenes_compra_message_id_key UNIQUE (message_id)'))
    conn.execute(text('ALTER TABLE "ComplementosPago" ALTER COLUMN fecha_pago SET NOT NULL'))
    conn.execute(text('ALTER TABLE "CorreosProcesados" ALTER COLUMN tipo_correo TYPE VARCHAR(30)'))
    conn.execute(text('ALTER TABLE "CorreosFallidos" DROP COLUMN IF EXISTS intentos'))

    # Alembic se cree en la cabeza, igual que en produccion.
    conn.execute(text("CREATE TABLE IF NOT EXISTS alembic_version "
                      "(version_num VARCHAR(32) NOT NULL PRIMARY KEY)"))
    conn.execute(text("DELETE FROM alembic_version"))
    conn.execute(text("INSERT INTO alembic_version VALUES ('8aa70d80b90e')"))

    # Dos fallos historicos, como los ~1,700 del VPS.
    conn.execute(text("""
        INSERT INTO "CorreosFallidos" (message_id, error, fecha_fallo, resuelto)
        VALUES ('MSG-A', '<HttpError 403 rate limit>', now(), 0),
               ('MSG-B', '<HttpError 404 not found>', now(), 0)
    """))

with motor.connect() as conn:
    afirmar(uniques(conn, "Facturas", "message_id") != [],
            "estado inicial: Facturas_message_id_key presente")
    afirmar(uniques(conn, "ComplementosPago", "message_id") != [],
            "estado inicial: ComplementosPago_message_id_key presente")
    afirmar(columna(conn, "ComplementosPago", "fecha_pago")[0] == "NO",
            "estado inicial: fecha_pago NOT NULL")
    afirmar(columna(conn, "CorreosProcesados", "tipo_correo")[1] == 30,
            "estado inicial: tipo_correo VARCHAR(30)")
    afirmar(columna(conn, "CorreosFallidos", "intentos") is None,
            "estado inicial: sin columna intentos")


# ─────────────────────────── 2. Correr la migracion de verdad

print("\n[2] python -m alembic upgrade head")

proceso = subprocess.run(
    [sys.executable, "-c", "from alembic.config import main; main()", "upgrade", "head"],
    capture_output=True, text=True, env={**os.environ},
)
print(proceso.stdout.strip() or "(sin stdout)")
if proceso.returncode != 0:
    print(proceso.stderr[-3000:])
afirmar(proceso.returncode == 0, "alembic upgrade head termina sin error")


# ─────────────────────────── 3. Verificar el resultado

print("\n[3] Estado despues de la migracion")

with motor.connect() as conn:
    afirmar(uniques(conn, "Facturas", "message_id") == [],
            "UNIQUE de Facturas.message_id eliminado")
    afirmar(uniques(conn, "ComplementosPago", "message_id") == [],
            "UNIQUE de ComplementosPago.message_id eliminado")
    afirmar(uniques(conn, "ordenes_compra", "message_id") == [],
            "UNIQUE de ordenes_compra.message_id eliminado")
    afirmar(uniques(conn, "ComplementosPago", "hash_archivo") == [],
            "UNIQUE de ComplementosPago.hash_archivo eliminado")

    afirmar(uniques(conn, "ComplementosPago", "uuid_cp") != [],
            "UNIQUE de uuid_cp CONSERVADO (es la llave natural del CP)")
    afirmar(uniques(conn, "ordenes_compra", "hash_archivo") != [],
            "UNIQUE de ordenes_compra.hash_archivo CONSERVADO (dedupe de OCs)")
    afirmar(uniques(conn, "CorreosProcesados", "message_id") != [],
            "UNIQUE de CorreosProcesados.message_id CONSERVADO (marca de procesado)")

    for indice in ("ix_facturas_message_id", "ix_cp_message_id",
                   "ix_oc_message_id", "ix_cp_hash_archivo",
                   "ix_correos_fallidos_resuelto", "ix_correos_fallidos_message_id"):
        afirmar(indice_existe(conn, indice), f"indice {indice} creado")

    afirmar(columna(conn, "ComplementosPago", "fecha_pago")[0] == "YES",
            "fecha_pago ahora es nullable")
    afirmar(columna(conn, "CorreosProcesados", "tipo_correo")[1] == 120,
            "tipo_correo ahora es VARCHAR(120)")
    afirmar(columna(conn, "CorreosFallidos", "intentos") is not None,
            "columna intentos creada")

    intentos = conn.execute(text(
        'SELECT DISTINCT intentos FROM "CorreosFallidos"'
    )).scalars().all()
    afirmar(intentos == [1], f"los fallos historicos quedaron en intentos=1 ({intentos})")


# ─────────────────────────── 4. Lo que antes reventaba

print("\n[4] Lo que la base prohibia y ahora acepta")

with motor.begin() as conn:
    conn.execute(text("""
        INSERT INTO "Estados" (nombre_estado, descripcion_estado)
        VALUES ('Pendiente de CP', 'x')
    """))
    conn.execute(text("""
        INSERT INTO "Usuarios" (nombre, correo, password_hash, rol)
        VALUES ('Sistema Automatico', 's@m.test', 'x', 'sistema')
    """))

with motor.begin() as conn:
    try:
        conn.execute(text("""
            INSERT INTO "Facturas"
              (folio_fiscal, rfc, cliente, fecha, moneda, id_usuario, id_estado, message_id)
            SELECT 'UUID-1', 'XAXX010101000', 'DEMO', '2026-09-01', 'MXN',
                   u.id_usuario, e.id_estado, 'MSG-MISMO'
            FROM "Usuarios" u, "Estados" e LIMIT 1
        """))
        conn.execute(text("""
            INSERT INTO "Facturas"
              (folio_fiscal, rfc, cliente, fecha, moneda, id_usuario, id_estado, message_id)
            SELECT 'UUID-2', 'XAXX010101000', 'DEMO', '2026-09-01', 'MXN',
                   u.id_usuario, e.id_estado, 'MSG-MISMO'
            FROM "Usuarios" u, "Estados" e LIMIT 1
        """))
        ok_facturas = True
    except Exception as e:
        ok_facturas = False
        print("   ", e)
afirmar(ok_facturas, "dos facturas con el mismo message_id ya entran")

with motor.begin() as conn:
    try:
        conn.execute(text("""
            INSERT INTO "ComplementosPago" (uuid_cp, folio, fecha_pago, message_id, cancelado)
            VALUES ('CP-1', 'CP576', now(), 'MSG-CP', false),
                   ('CP-2', 'CP577', now(), 'MSG-CP', false)
        """))
        ok_cps = True
    except Exception as e:
        ok_cps = False
        print("   ", e)
afirmar(ok_cps, "dos CPs con el mismo message_id ya entran")

with motor.begin() as conn:
    try:
        conn.execute(text("""
            INSERT INTO "ComplementosPago" (uuid_cp, folio, fecha_pago, message_id, cancelado)
            VALUES ('CP-3', 'CP578', NULL, 'MSG-CP', false)
        """))
        ok_sin_fecha = True
    except Exception as e:
        ok_sin_fecha = False
        print("   ", e)
afirmar(ok_sin_fecha, "un CP sin fecha de pago ya entra")

with motor.begin() as conn:
    try:
        conn.execute(text("""
            INSERT INTO "CorreosProcesados" (message_id, tipo_correo, fecha_procesado)
            VALUES ('MSG-LARGO', 'facturas:1+complementos:1+ordenes:1+errores:1', now())
        """))
        ok_largo = True
    except Exception as e:
        ok_largo = False
        print("   ", e)
afirmar(ok_largo, "el tipo_correo de 44 caracteres ya cabe (antes desbordaba a los 30)")

with motor.begin() as conn:
    try:
        conn.execute(text("""
            INSERT INTO "ComplementosPago" (uuid_cp, folio, fecha_pago, message_id, cancelado)
            VALUES ('CP-1', 'CP999', now(), 'OTRO', false)
        """))
        rechaza_duplicado = False
    except Exception:
        rechaza_duplicado = True
afirmar(rechaza_duplicado, "el mismo uuid_cp SIGUE rechazado: el dedupe real no se toco")


# ─────────────────────────── 5. Idempotencia y downgrade

print("\n[5] Repetir y revertir")

modulo = __import__(
    "alembic.versions.c4f1a9b27d30_arreglo_multidocumento_cp", fromlist=["upgrade"]
) if False else None

from alembic.migration import MigrationContext
from alembic.operations import Operations
import importlib.util

ruta = "alembic/versions/c4f1a9b27d30_arreglo_multidocumento_cp.py"
especificacion = importlib.util.spec_from_file_location("migracion_cp", ruta)
migracion = importlib.util.module_from_spec(especificacion)
especificacion.loader.exec_module(migracion)

with motor.begin() as conn:
    contexto = MigrationContext.configure(conn)
    with Operations.context(contexto):
        try:
            migracion.upgrade()
            repetible = True
        except Exception as e:
            repetible = False
            print("   ", e)
afirmar(repetible, "correr upgrade() dos veces no revienta (es idempotente)")

proceso = subprocess.run(
    [sys.executable, "-c", "from alembic.config import main; main()", "downgrade", "-1"],
    capture_output=True, text=True, env={**os.environ},
)
if proceso.returncode != 0:
    print(proceso.stderr[-1500:])
# Falla a proposito: ya hay dos facturas con el mismo message_id y un CP sin
# fecha de pago. Revertir sin limpiar primero DEBE fallar.
afirmar(proceso.returncode != 0,
        "downgrade se niega mientras existan datos multi-documento (proteccion buscada)")

print()
if fallos:
    print(f"{len(fallos)} PRUEBA(S) FALLIDA(S):")
    for f in fallos:
        print("  -", f)
    sys.exit(1)
print("Todas las pruebas de la migracion pasaron.")
