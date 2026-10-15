"""OCs descartadas (facturas, cotizaciones o duplicados que entraron como OC)

Revision ID: d8b4f1a6c372
Revises: c5e2f8a4b913
Create Date: 2026-10-15

No borra nada. Marca, con:  python -m app.services.redetectar_oc --aplicar
  - los PDFs guardados como OC que no lo son (facturas timbradas,
    cotizaciones, recibos);
y reconciliar() marca los reenvíos idénticos de una misma OC.
"""
from alembic import op
import sqlalchemy as sa


revision = "d8b4f1a6c372"
down_revision = "c5e2f8a4b913"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("ordenes_compra", sa.Column(
        "descartada", sa.Boolean(), nullable=False, server_default=sa.text("false")))
    op.add_column("ordenes_compra", sa.Column("motivo_descarte", sa.String(120), nullable=True))


def downgrade() -> None:
    op.drop_column("ordenes_compra", "motivo_descarte")
    op.drop_column("ordenes_compra", "descartada")
