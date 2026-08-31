"""columnas espejo sat

Revision ID: c8ed6e8df2bd
Revises: 999135afeeb2
Create Date: 2026-08-30 15:35:28.380552

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c8ed6e8df2bd'
down_revision: Union[str, Sequence[str], None] = '999135afeeb2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Agregar columnas espejo del webservice de consulta de estatus del SAT."""
    op.add_column("Facturas", sa.Column("sat_estado", sa.String(20), nullable=True))
    op.add_column("Facturas", sa.Column("sat_es_cancelable", sa.String(50), nullable=True))
    op.add_column("Facturas", sa.Column("sat_estatus_cancelacion", sa.String(50), nullable=True))
    op.add_column("Facturas", sa.Column("sat_codigo_estatus", sa.String(100), nullable=True))
    op.add_column("Facturas", sa.Column("sat_validacion_efos", sa.String(10), nullable=True))
    op.add_column(
        "Facturas",
        sa.Column("fecha_ultima_verificacion_sat", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "Facturas",
        sa.Column(
            "intentos_verificacion_fallidos",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )


def downgrade() -> None:
    """Quitar columnas espejo del SAT."""
    op.drop_column("Facturas", "intentos_verificacion_fallidos")
    op.drop_column("Facturas", "fecha_ultima_verificacion_sat")
    op.drop_column("Facturas", "sat_validacion_efos")
    op.drop_column("Facturas", "sat_codigo_estatus")
    op.drop_column("Facturas", "sat_estatus_cancelacion")
    op.drop_column("Facturas", "sat_es_cancelable")
    op.drop_column("Facturas", "sat_estado")