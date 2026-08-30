"""estado_historico

Revision ID: 999135afeeb2
Revises: 3c91f3e119df
Create Date: 2026-08-29 23:07:52.782689

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '999135afeeb2'
down_revision: Union[str, Sequence[str], None] = '3c91f3e119df'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        INSERT INTO "Estados" (nombre_estado, descripcion_estado)
        VALUES ('Histórico',
                'Registro migrado del Excel previo al sistema. No participa en el flujo de captura.')
    """)


def downgrade() -> None:
    op.execute("""DELETE FROM "Estados" WHERE nombre_estado = 'Histórico'""")
