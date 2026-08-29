"""estado_revisada_multifactura

Revision ID: bce9ea1ab852
Revises: 869e90832d5f
Create Date: 2026-08-22 13:22:36.164678

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'bce9ea1ab852'
down_revision: Union[str, Sequence[str], None] = '869e90832d5f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

CONSTRAINT_FACTURAS_MSG = "Facturas_message_id_key"
CONSTRAINT_CP_MSG = "ComplementosPago_message_id_key"
 
NOMBRE_ESTADO = "Revisada"
DESCRIPCION_ESTADO = "Ciclo completo verificado y confirmado manualmente"


def upgrade():
    # === 1. Quitar UNIQUE de message_id ===
    # Un solo correo puede traer varias facturas / varios CPs.
    # El dedupe real es por folio_fiscal y uuid_cp, no por message_id.
    op.drop_constraint(CONSTRAINT_FACTURAS_MSG, "Facturas", type_="unique")
    op.drop_constraint(CONSTRAINT_CP_MSG, "ComplementosPago", type_="unique")
 
    # Índices no únicos: message_id se sigue consultando, solo deja de ser único
    op.create_index("ix_facturas_message_id", "Facturas", ["message_id"])
    op.create_index("ix_cp_message_id", "ComplementosPago", ["message_id"])
 
    # === 2. Estado "Revisada" ===
    op.execute(
        sa.text(
            """
            INSERT INTO "Estados" ("nombre_estado", "descripcion_estado")
            SELECT :nombre, :descripcion
            WHERE NOT EXISTS (
                SELECT 1 FROM "Estados" WHERE "nombre_estado" = :nombre
            )
            """
        ).bindparams(nombre=NOMBRE_ESTADO, descripcion=DESCRIPCION_ESTADO)
    )


def downgrade():
    op.execute(
        sa.text("""DELETE FROM "Estados" WHERE "nombre_estado" = :nombre""")
        .bindparams(nombre=NOMBRE_ESTADO)
    )
 
    op.drop_index("ix_cp_message_id", table_name="ComplementosPago")
    op.drop_index("ix_facturas_message_id", table_name="Facturas")
 
    # Falla si ya hay message_id duplicados (correos multi-factura procesados).
    # Es intencional: hay que limpiarlos antes de revertir.
    op.create_unique_constraint(CONSTRAINT_CP_MSG, "ComplementosPago", ["message_id"])
    op.create_unique_constraint(CONSTRAINT_FACTURAS_MSG, "Facturas", ["message_id"])
