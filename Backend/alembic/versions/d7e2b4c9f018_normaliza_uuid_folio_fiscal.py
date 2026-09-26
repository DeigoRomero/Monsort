"""normaliza_uuid_folio_fiscal

Pone en MAYUSCULAS y sin espacios todo folio fiscal ya guardado.

El SAT especifica el UUID en mayusculas, pero no todos los emisores lo
respetan: hay PACs que escriben el IdDocumento de un DoctoRelacionado en
minusculas. Como reconciliar() compara con == y Postgres distingue
mayusculas, el mismo documento con distinta caja no se encontraba nunca.

Medido en produccion el 26/09/2026: de 46 documentos de CP, 44 estaban sin
vincular y 23 de ellos pegaban perfecto ignorando la caja. Las 755 facturas
ya estaban todas en mayusculas, asi que el desajuste venia del lado del CP.

Esta migracion solo toca datos, no esquema. Es idempotente: correrla dos
veces no cambia nada la segunda vez.

El indice funcional del final es para que las busquedas por UUID sigan
usando indice aunque alguien compare con upper() en el futuro.

Revision ID: d7e2b4c9f018
Revises: c4f1a9b27d30
Create Date: 2026-09-26

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'd7e2b4c9f018'
down_revision: Union[str, Sequence[str], None] = 'c4f1a9b27d30'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# (tabla, columna) de cada folio fiscal del proyecto
COLUMNAS_UUID = [
    ('"Facturas"', 'folio_fiscal'),
    ('"ComplementosPago"', 'uuid_cp'),
    ('cp_documentos_relacionados', 'uuid_documento'),
]

SQL_INDICE_EXISTE = sa.text("""
    SELECT 1 FROM pg_class cls
    JOIN pg_namespace ns ON ns.oid = cls.relnamespace
    WHERE cls.relname = :nombre AND cls.relkind = 'i'
      AND ns.nspname = ANY (current_schemas(false))
""")

SQL_TABLA_EXISTE = sa.text("""
    SELECT 1 FROM information_schema.tables
    WHERE table_name = :tabla
      AND table_schema = ANY (current_schemas(false))
""")


def _tabla_existe(bind, tabla: str) -> bool:
    limpio = tabla.strip('"')
    return bind.execute(SQL_TABLA_EXISTE, {"tabla": limpio}).first() is not None


def upgrade() -> None:
    bind = op.get_bind()

    for tabla, columna in COLUMNAS_UUID:
        if not _tabla_existe(bind, tabla):
            print(f"    · {tabla} no existe, se omite")
            continue

        resultado = bind.execute(sa.text(
            f"UPDATE {tabla} SET {columna} = upper(btrim({columna})) "
            f"WHERE {columna} IS NOT NULL AND {columna} <> upper(btrim({columna}))"
        ))
        print(f"    - {tabla}.{columna}: {resultado.rowcount} fila(s) normalizada(s)")

    # FacturasRecibidas tambien guarda folio fiscal, con otro nombre de columna
    # segun la version del modelo. Se normaliza si la columna esta.
    for columna in ("folio_fiscal", "uuid"):
        existe = bind.execute(sa.text("""
            SELECT 1 FROM information_schema.columns
            WHERE table_name = 'FacturasRecibidas' AND column_name = :c
              AND table_schema = ANY (current_schemas(false))
        """), {"c": columna}).first()
        if existe:
            resultado = bind.execute(sa.text(
                f'UPDATE "FacturasRecibidas" SET {columna} = upper(btrim({columna})) '
                f"WHERE {columna} IS NOT NULL AND {columna} <> upper(btrim({columna}))"
            ))
            print(f'    - "FacturasRecibidas".{columna}: '
                  f"{resultado.rowcount} fila(s) normalizada(s)")

    if not bind.execute(SQL_INDICE_EXISTE, {"nombre": "ix_facturas_folio_fiscal"}).first():
        op.create_index("ix_facturas_folio_fiscal", "Facturas", ["folio_fiscal"])

    if not bind.execute(SQL_INDICE_EXISTE,
                        {"nombre": "ix_cp_docs_uuid_documento"}).first():
        op.create_index("ix_cp_docs_uuid_documento",
                        "cp_documentos_relacionados", ["uuid_documento"])


def downgrade() -> None:
    # No se revierte: no hay registro de cual era la caja original de cada
    # UUID, y volver a minusculas seria inventar. La normalizacion no pierde
    # informacion fiscal, solo uniforma la representacion.
    bind = op.get_bind()

    if bind.execute(SQL_INDICE_EXISTE, {"nombre": "ix_cp_docs_uuid_documento"}).first():
        op.drop_index("ix_cp_docs_uuid_documento",
                      table_name="cp_documentos_relacionados")

    if bind.execute(SQL_INDICE_EXISTE, {"nombre": "ix_facturas_folio_fiscal"}).first():
        op.drop_index("ix_facturas_folio_fiscal", table_name="Facturas")
