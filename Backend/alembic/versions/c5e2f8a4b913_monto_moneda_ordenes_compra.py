"""Monto y moneda en ordenes_compra (filtros de la pantalla de OCs)

Revision ID: c5e2f8a4b913
Revises: a7c3e9d1f250
Create Date: 2026-10-14

Columnas nuevas, vacías al crearse. Se llenan:
  - en OCs nuevas, al procesar el correo (detector_oc.detectar_monto_oc);
  - en las existentes, con:  python -m app.services.redetectar_oc --aplicar
id_cliente ya existía; el mismo script lo llena donde está vacío.
"""
from alembic import op
import sqlalchemy as sa


revision = "c5e2f8a4b913"
down_revision = "a7c3e9d1f250"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("ordenes_compra", sa.Column("monto", sa.Numeric(14, 2), nullable=True))
    op.add_column("ordenes_compra", sa.Column("moneda", sa.String(3), nullable=True))
    op.create_index("ix_ordenes_compra_fecha_recepcion", "ordenes_compra", ["fecha_recepcion"])


def downgrade() -> None:
    op.drop_index("ix_ordenes_compra_fecha_recepcion", table_name="ordenes_compra")
    op.drop_column("ordenes_compra", "moneda")
    op.drop_column("ordenes_compra", "monto")
