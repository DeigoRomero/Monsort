"""facturas recibidas

Revision ID: 8aa70d80b90e
Revises: 9b3628f270e4
Create Date: 2026-09-10 23:08:57.710585

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '8aa70d80b90e'
down_revision: Union[str, Sequence[str], None] = '9b3628f270e4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade():
    op.create_table(
        "FacturasRecibidas",
        sa.Column("id_factura_recibida", sa.Integer(), nullable=False, autoincrement=True),
 
        # --- Hechos de emision. Propiedad de la ingesta. Nunca se pisan. ---
        sa.Column("folio_fiscal", sa.String(length=36), nullable=False),
        sa.Column("rfc_emisor", sa.String(length=13), nullable=False),
        sa.Column("nombre_emisor", sa.String(length=254), nullable=True),
        sa.Column("rfc_receptor", sa.String(length=13), nullable=False),
        sa.Column("nombre_receptor", sa.String(length=254), nullable=True),
        sa.Column("fecha_emision", sa.DateTime(timezone=False), nullable=False),
        sa.Column("monto_total", sa.DECIMAL(precision=18, scale=6), nullable=False),
        sa.Column("efecto_comprobante", sa.String(length=1), nullable=True),
        sa.Column("rfc_pac", sa.String(length=13), nullable=True),
        sa.Column("origen", sa.String(length=20), nullable=False, server_default="sat"),
 
        # --- Hechos de estatus. Propiedad de la verificacion. ---
        sa.Column("fecha_cancelacion", sa.DateTime(timezone=False), nullable=True),
        sa.Column("sat_estado", sa.String(length=20), nullable=True),
        sa.Column("sat_es_cancelable", sa.String(length=50), nullable=True),
        sa.Column("sat_estatus_cancelacion", sa.String(length=50), nullable=True),
        sa.Column("sat_codigo_estatus", sa.String(length=100), nullable=True),
        sa.Column("sat_validacion_efos", sa.String(length=10), nullable=True),
        sa.Column("fecha_ultima_verificacion_sat", sa.DateTime(timezone=True), nullable=True),
        sa.Column("intentos_verificacion_fallidos", sa.Integer(), nullable=False,
                  server_default="0"),
 
        # --- Trazabilidad ---
        sa.Column("id_solicitud", sa.Integer(), nullable=True),
        sa.Column("creado_en", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("actualizado_en", sa.DateTime(timezone=True), nullable=True),
 
        sa.PrimaryKeyConstraint("id_factura_recibida"),
        sa.ForeignKeyConstraint(["id_solicitud"], ["SolicitudesSAT.id"]),
    )
 
    # El unico de folio_fiscal NO es opcional: es el conflict target de
    # on_conflict_do_update en la ingesta idempotente.
    op.create_index(
        "ix_FacturasRecibidas_folio_fiscal", "FacturasRecibidas",
        ["folio_fiscal"], unique=True,
    )
    op.create_index(
        "ix_FacturasRecibidas_id_factura_recibida", "FacturasRecibidas",
        ["id_factura_recibida"], unique=False,
    )
    op.create_index(
        "ix_FacturasRecibidas_rfc_emisor", "FacturasRecibidas",
        ["rfc_emisor"], unique=False,
    )
    op.create_index(
        "ix_FacturasRecibidas_fecha_emision", "FacturasRecibidas",
        ["fecha_emision"], unique=False,
    )
    op.create_index(
        "ix_FacturasRecibidas_efecto_comprobante", "FacturasRecibidas",
        ["efecto_comprobante"], unique=False,
    )
    # Compuesto: "todo lo de tal proveedor en tal rango"
    op.create_index(
        "ix_facturas_recibidas_emisor_fecha", "FacturasRecibidas",
        ["rfc_emisor", "fecha_emision"], unique=False,
    )


def downgrade():
    op.drop_index("ix_facturas_recibidas_emisor_fecha", table_name="FacturasRecibidas")
    op.drop_index("ix_FacturasRecibidas_efecto_comprobante", table_name="FacturasRecibidas")
    op.drop_index("ix_FacturasRecibidas_fecha_emision", table_name="FacturasRecibidas")
    op.drop_index("ix_FacturasRecibidas_rfc_emisor", table_name="FacturasRecibidas")
    op.drop_index("ix_FacturasRecibidas_id_factura_recibida", table_name="FacturasRecibidas")
    op.drop_index("ix_FacturasRecibidas_folio_fiscal", table_name="FacturasRecibidas")
    op.drop_table("FacturasRecibidas")
