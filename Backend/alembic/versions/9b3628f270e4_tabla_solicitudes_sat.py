"""tabla solicitudes sat

Revision ID: 9b3628f270e4
Revises: c8ed6e8df2bd
Create Date: 2026-08-30 21:48:00.645229

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '9b3628f270e4'
down_revision: Union[str, Sequence[str], None] = 'c8ed6e8df2bd'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "SolicitudesSAT",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("fecha_inicial", sa.Date(), nullable=False),
        sa.Column("fecha_final", sa.Date(), nullable=False),
        sa.Column("tipo_solicitud", sa.String(20), nullable=False),
        sa.Column("tipo_comprobante", sa.String(20), nullable=False, server_default="emitidos"),
        sa.Column("estado", sa.String(20), nullable=False, server_default="NUEVA"),
        sa.Column("id_solicitud_sat", sa.String(50), nullable=True),
        sa.Column("estado_solicitud_sat", sa.Integer(), nullable=True),
        sa.Column("codigo_estatus", sa.String(10), nullable=True),
        sa.Column("mensaje_sat", sa.String(255), nullable=True),
        sa.Column("numero_cfdis", sa.Integer(), nullable=True),
        sa.Column("paquetes", sa.Text(), nullable=True),
        sa.Column("paquetes_descargados", sa.Text(), nullable=True),
        sa.Column("intentos_verificacion", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("fecha_creacion", sa.DateTime(timezone=True), nullable=False),
        sa.Column("fecha_ultimo_intento", sa.DateTime(timezone=True), nullable=True),
        sa.Column("fecha_completada", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cfdis_nuevos", sa.Integer(), nullable=True),
        sa.Column("cfdis_duplicados", sa.Integer(), nullable=True),
        sa.Column("error_ingesta", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("id_solicitud_sat"),
    )
    op.create_index("ix_solicitudes_sat_estado", "SolicitudesSAT", ["estado"])
    op.create_index("ix_solicitudes_sat_id_sat", "SolicitudesSAT", ["id_solicitud_sat"])


def downgrade() -> None:
    op.drop_index("ix_solicitudes_sat_id_sat", table_name="SolicitudesSAT")
    op.drop_index("ix_solicitudes_sat_estado", table_name="SolicitudesSAT")
    op.drop_table("SolicitudesSAT")
