"""cancelacion_moneda_validacion

Revision ID: 869e90832d5f
Revises: fb379c199137
Create Date: 2026-08-18 22:06:19.824533

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '869e90832d5f'
down_revision: Union[str, Sequence[str], None] = 'fb379c199137'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLA_CP = "ComplementosPago"        # consulta 1
COLUMNA_NOMBRE_ESTADO = "nombre_estado"     # consulta 2  (¿nombre? ¿descripcion? ¿estado?)
NOMBRE_ESTADO_CANCELADA = "Cancelada"


def upgrade():
    # === 1. HistorialVerificacion: distinguir origen manual vs SAT ===
    op.add_column(
        "HistorialVerificacion",
        sa.Column("origen", sa.String(length=20), nullable=False, server_default="manual"),
    )
 
    # === 2. ComplementosPago: cancelación + auditoría ===
    op.add_column(
        TABLA_CP,
        sa.Column("cancelado", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(TABLA_CP, sa.Column("fecha_cancelacion", sa.Date(), nullable=True))
    op.add_column(TABLA_CP, sa.Column("motivo_cancelacion", sa.String(), nullable=True))
    op.add_column(TABLA_CP, sa.Column("cancelado_por", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "fk_cp_cancelado_por_usuario",
        TABLA_CP,
        "Usuarios",
        ["cancelado_por"],
        ["id_usuario"],
    )
 
    # === 3. Índice sobre Facturas.fecha (filtro más usado del sistema) ===
    op.create_index("ix_facturas_fecha", "Facturas", ["fecha"])
 
    # === 4. Data migration: estado "Cancelada" ===
    # Sin id explícito para no desincronizar la secuencia autoincremental.
    # Idempotente: re-ejecutar no duplica.
    op.execute(
    sa.text(
        f"""
        INSERT INTO "Estados" ("{COLUMNA_NOMBRE_ESTADO}", "descripcion_estado")
        SELECT :nombre, :descripcion
        WHERE NOT EXISTS (
            SELECT 1 FROM "Estados"
            WHERE "{COLUMNA_NOMBRE_ESTADO}" = :nombre
        )
        """
    ).bindparams(
        nombre=NOMBRE_ESTADO_CANCELADA,
        descripcion="Factura o CP cancelado administrativamente"
    )
)


def downgrade():
    # El DELETE falla intencionalmente si alguna factura o CP ya quedó
    # en estado Cancelada — hay que reasignarlos antes.
    op.execute(
        sa.text(
            f"""
            DELETE FROM "Estados"
            WHERE "{COLUMNA_NOMBRE_ESTADO}" = :nombre
            """
        ).bindparams(nombre=NOMBRE_ESTADO_CANCELADA)
    )
 
    op.drop_constraint("fk_cp_cancelado_por_usuario", TABLA_CP, type_="foreignkey")
    op.drop_column(TABLA_CP, "cancelado_por")
    op.drop_column(TABLA_CP, "motivo_cancelacion")
    op.drop_column(TABLA_CP, "fecha_cancelacion")
    op.drop_column(TABLA_CP, "cancelado")
 
    op.drop_index("ix_facturas_fecha", table_name="Facturas")
 
    op.drop_column("HistorialVerificacion", "origen")
