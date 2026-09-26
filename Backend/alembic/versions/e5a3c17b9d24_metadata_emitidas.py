"""metadata_emitidas

Tabla para guardar la metadata de EMITIDAS que el SAT devuelve.

Antes `ingerir_metadata()` descargaba el paquete, contaba los UUID contra
`Facturas` y tiraba el archivo. El TXT trae la FechaEmision de cada
comprobante, y sin guardarla no hay forma de saber de que mes es una factura
ausente cuyo pago (CP) si llego: un UUID no lleva fecha adentro. El 26/09/2026
eso dejo 10 pagos sin poder resolver sin adivinar el rango de la solicitud.

Guardarla convierte la metadata en el indice BARATO del SAT (su solicitud no
consume el limite de por vida, a diferencia de las de tipo 'cfdi') y habilita
la reconciliacion permanente: "el SAT dice que emitiste 112 en agosto y tienes
105".

Estructura calcada de FacturasRecibidas a proposito: mismos nombres de columna
para que el parser de metadata sirva para las dos direcciones sin bifurcarse.

Revision ID: e5a3c17b9d24
Revises: d7e2b4c9f018
Create Date: 2026-09-26

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'e5a3c17b9d24'
down_revision: Union[str, Sequence[str], None] = 'd7e2b4c9f018'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


SQL_TABLA_EXISTE = sa.text("""
    SELECT 1 FROM information_schema.tables
    WHERE table_name = 'MetadataEmitidas'
      AND table_schema = ANY (current_schemas(false))
""")


def upgrade() -> None:
    bind = op.get_bind()

    if bind.execute(SQL_TABLA_EXISTE).first():
        print("    · MetadataEmitidas ya existe, se omite")
        return

    op.create_table(
        "MetadataEmitidas",
        sa.Column("id", sa.Integer(), nullable=False),

        # Hechos de emision: la ingesta los escribe una vez y no los pisa.
        sa.Column("folio_fiscal", sa.String(length=36), nullable=False),
        sa.Column("rfc_emisor", sa.String(length=13), nullable=False),
        sa.Column("nombre_emisor", sa.String(length=254), nullable=True),
        sa.Column("rfc_receptor", sa.String(length=13), nullable=False),
        sa.Column("nombre_receptor", sa.String(length=254), nullable=True),
        sa.Column("fecha_emision", sa.DateTime(timezone=False), nullable=False),
        # 6 decimales: los manda asi el SAT, truncar seria perder informacion
        sa.Column("monto_total", sa.DECIMAL(precision=18, scale=6), nullable=False),
        sa.Column("efecto_comprobante", sa.String(length=1), nullable=True),
        sa.Column("rfc_pac", sa.String(length=13), nullable=True),

        # Hechos de estatus: el barrido de metadata los refresca.
        sa.Column("sat_estado", sa.String(length=20), nullable=True),
        sa.Column("fecha_cancelacion", sa.DateTime(timezone=False), nullable=True),

        # Trazabilidad
        sa.Column("id_solicitud", sa.Integer(), nullable=True),
        sa.Column("ingerido_en", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=True),
        sa.Column("actualizado_en", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=True),

        sa.ForeignKeyConstraint(["id_solicitud"], ["SolicitudesSAT.id"]),
        sa.PrimaryKeyConstraint("id"),
        # UNIQUE y no solo indice: es la llave del UPSERT de la ingesta.
        sa.UniqueConstraint("folio_fiscal"),
    )

    op.create_index(op.f("ix_MetadataEmitidas_id"), "MetadataEmitidas", ["id"])
    op.create_index("ix_metadata_emitidas_folio_fiscal",
                    "MetadataEmitidas", ["folio_fiscal"])
    op.create_index("ix_metadata_emitidas_fecha_emision",
                    "MetadataEmitidas", ["fecha_emision"])
    op.create_index("ix_metadata_emitidas_rfc_receptor",
                    "MetadataEmitidas", ["rfc_receptor"])
    op.create_index("ix_metadata_emitidas_efecto",
                    "MetadataEmitidas", ["efecto_comprobante"])
    # La consulta que importa: cobertura y faltantes por mes.
    op.create_index("ix_metadata_emitidas_fecha_estado",
                    "MetadataEmitidas", ["fecha_emision", "sat_estado"])

    print("    - Tabla MetadataEmitidas creada")


def downgrade() -> None:
    bind = op.get_bind()

    if not bind.execute(SQL_TABLA_EXISTE).first():
        return

    # Solo guarda el dicho del SAT, que se puede volver a pedir con metadata
    # (la solicitud barata). Tirarla no pierde nada irrecuperable.
    op.drop_table("MetadataEmitidas")
