"""arreglo_multidocumento_cp

Termina el rediseño multi-documento que quedó a medias: el código procesa
varios CFDI por correo desde `fe748321bb59`, pero la base seguía prohibiendo
más de uno. La migración `bce9ea1ab852` debía quitar esos UNIQUE y su DDL
nunca se ejecutó contra esta base (los constraints siguen vivos en
producción, verificado en pg_constraint el 2026-09-26).

Por eso NO se usa drop_constraint con el nombre a ciegas: se busca el
constraint real en el catálogo y se tira si existe. Así la migración corre
igual en una base donde `bce9ea1ab852` sí se aplicó, en una donde no, y en
una restaurada de un dump viejo.

Cambios:
  1. Quita UNIQUE(message_id) de Facturas, ComplementosPago y ordenes_compra.
     Un correo puede traer N documentos; el dedupe real es folio_fiscal /
     uuid_cp / hash_archivo. Deja índice no único: message_id se sigue
     consultando.
  2. Quita UNIQUE(hash_archivo) de ComplementosPago. La llave natural del CP
     es uuid_cp; el hash del PDF no identifica el documento fiscal.
     (El de ordenes_compra SÍ se conserva: ahí el código lo usa para dedupe.)
  3. ComplementosPago.fecha_pago pasa a nullable. Un CP sin nodo Pago
     parseable (namespace Pagos10, XML raro) debe poder guardarse para
     revisión manual en vez de tumbar el correo completo.
  4. CorreosProcesados.tipo_correo de VARCHAR(30) a VARCHAR(120).
     "facturas:1+complementos:1+ordenes:1" mide 35 y el insert va DESPUÉS de
     guardar los documentos: al desbordar, el rollback se llevaba todo.
  5. CorreosFallidos.intentos, para que el reproceso automático no reintente
     en bucle un correo que nunca va a pasar.

Revision ID: c4f1a9b27d30
Revises: 8aa70d80b90e
Create Date: 2026-09-26

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'c4f1a9b27d30'
down_revision: Union[str, Sequence[str], None] = '8aa70d80b90e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# ─────────────────────────────────────────────────────────────────────────
# HELPERS DE CATÁLOGO
#
# Todo lo que sigue consulta el estado real de la base antes de tocarla.
# Idempotente a propósito: se puede correr dos veces sin reventar.
# ─────────────────────────────────────────────────────────────────────────

SQL_UNIQUES_DE_UNA_COLUMNA = sa.text("""
    SELECT con.conname
    FROM pg_constraint con
    JOIN pg_class rel ON rel.oid = con.conrelid
    JOIN pg_namespace ns ON ns.oid = rel.relnamespace
    WHERE rel.relname = :tabla
      AND ns.nspname = ANY (current_schemas(false))
      AND con.contype = 'u'
      AND array_length(con.conkey, 1) = 1
      AND con.conkey[1] = (
          SELECT att.attnum
          FROM pg_attribute att
          WHERE att.attrelid = con.conrelid
            AND att.attname = :columna
            AND att.attnum > 0
            AND NOT att.attisdropped
      )
""")

SQL_INDICE_EXISTE = sa.text("""
    SELECT 1
    FROM pg_class cls
    JOIN pg_namespace ns ON ns.oid = cls.relnamespace
    WHERE cls.relname = :nombre
      AND cls.relkind = 'i'
      AND ns.nspname = ANY (current_schemas(false))
""")

SQL_COLUMNA_EXISTE = sa.text("""
    SELECT 1
    FROM information_schema.columns
    WHERE table_name = :tabla
      AND column_name = :columna
      AND table_schema = ANY (current_schemas(false))
""")


def _quitar_unique(bind, tabla: str, columna: str) -> list[str]:
    """Tira todo UNIQUE de una sola columna sobre (tabla, columna)."""
    nombres = [
        fila[0] for fila in bind.execute(
            SQL_UNIQUES_DE_UNA_COLUMNA, {"tabla": tabla, "columna": columna}
        )
    ]
    for nombre in nombres:
        op.drop_constraint(nombre, tabla, type_="unique")
        print(f"    - UNIQUE quitado: {tabla}.{columna} ({nombre})")
    if not nombres:
        print(f"    · {tabla}.{columna}: sin UNIQUE que quitar")
    return nombres


def _poner_unique(bind, nombre: str, tabla: str, columna: str) -> None:
    """Recrea el UNIQUE solo si no hay ya uno equivalente (para downgrade)."""
    existentes = bind.execute(
        SQL_UNIQUES_DE_UNA_COLUMNA, {"tabla": tabla, "columna": columna}
    ).first()
    if not existentes:
        op.create_unique_constraint(nombre, tabla, [columna])


def _crear_indice(bind, nombre: str, tabla: str, columna: str) -> None:
    if not bind.execute(SQL_INDICE_EXISTE, {"nombre": nombre}).first():
        op.create_index(nombre, tabla, [columna])


def _borrar_indice(bind, nombre: str, tabla: str) -> None:
    if bind.execute(SQL_INDICE_EXISTE, {"nombre": nombre}).first():
        op.drop_index(nombre, table_name=tabla)


def _columna_existe(bind, tabla: str, columna: str) -> bool:
    return bind.execute(
        SQL_COLUMNA_EXISTE, {"tabla": tabla, "columna": columna}
    ).first() is not None


# ─────────────────────────────────────────────────────────────────────────

def upgrade() -> None:
    bind = op.get_bind()

    # --- 1. UNIQUE(message_id): un correo puede traer N documentos ---
    print("  [1/5] UNIQUE(message_id)")
    _quitar_unique(bind, "Facturas", "message_id")
    _quitar_unique(bind, "ComplementosPago", "message_id")
    _quitar_unique(bind, "ordenes_compra", "message_id")

    _crear_indice(bind, "ix_facturas_message_id", "Facturas", "message_id")
    _crear_indice(bind, "ix_cp_message_id", "ComplementosPago", "message_id")
    _crear_indice(bind, "ix_oc_message_id", "ordenes_compra", "message_id")

    # --- 2. UNIQUE(hash_archivo) del CP: la llave natural es uuid_cp ---
    print("  [2/5] UNIQUE(hash_archivo) de ComplementosPago")
    _quitar_unique(bind, "ComplementosPago", "hash_archivo")
    _crear_indice(bind, "ix_cp_hash_archivo", "ComplementosPago", "hash_archivo")

    # --- 3. fecha_pago nullable ---
    print("  [3/5] ComplementosPago.fecha_pago -> nullable")
    op.alter_column(
        "ComplementosPago", "fecha_pago",
        existing_type=sa.DateTime(),
        nullable=True,
    )

    # --- 4. tipo_correo más ancho ---
    print("  [4/5] CorreosProcesados.tipo_correo -> VARCHAR(120)")
    op.alter_column(
        "CorreosProcesados", "tipo_correo",
        existing_type=sa.String(length=30),
        type_=sa.String(length=120),
        existing_nullable=True,
    )

    # --- 5. Contador de intentos en CorreosFallidos ---
    print("  [5/5] CorreosFallidos.intentos")
    if not _columna_existe(bind, "CorreosFallidos", "intentos"):
        op.add_column(
            "CorreosFallidos",
            sa.Column("intentos", sa.Integer(), nullable=False, server_default="0"),
        )
        # Los ~1,700 fallos históricos vienen de un solo intento cada uno.
        op.execute('UPDATE "CorreosFallidos" SET intentos = 1 WHERE intentos = 0')

    _crear_indice(bind, "ix_correos_fallidos_resuelto", "CorreosFallidos", "resuelto")
    _crear_indice(bind, "ix_correos_fallidos_message_id", "CorreosFallidos", "message_id")


def downgrade() -> None:
    bind = op.get_bind()

    _borrar_indice(bind, "ix_correos_fallidos_message_id", "CorreosFallidos")
    _borrar_indice(bind, "ix_correos_fallidos_resuelto", "CorreosFallidos")
    if _columna_existe(bind, "CorreosFallidos", "intentos"):
        op.drop_column("CorreosFallidos", "intentos")

    op.alter_column(
        "CorreosProcesados", "tipo_correo",
        existing_type=sa.String(length=120),
        type_=sa.String(length=30),
        existing_nullable=True,
    )

    # Falla si hay CPs con fecha_pago NULL. Es intencional: hay que
    # capturarlos a mano antes de revertir.
    op.alter_column(
        "ComplementosPago", "fecha_pago",
        existing_type=sa.DateTime(),
        nullable=False,
    )

    _borrar_indice(bind, "ix_cp_hash_archivo", "ComplementosPago")
    _poner_unique(bind, "ComplementosPago_hash_archivo_key",
                  "ComplementosPago", "hash_archivo")

    _borrar_indice(bind, "ix_oc_message_id", "ordenes_compra")
    _borrar_indice(bind, "ix_cp_message_id", "ComplementosPago")
    _borrar_indice(bind, "ix_facturas_message_id", "Facturas")

    # Falla si ya se procesaron correos multi-documento. También intencional.
    _poner_unique(bind, "ordenes_compra_message_id_key",
                  "ordenes_compra", "message_id")
    _poner_unique(bind, "ComplementosPago_message_id_key",
                  "ComplementosPago", "message_id")
    _poner_unique(bind, "Facturas_message_id_key",
                  "Facturas", "message_id")
