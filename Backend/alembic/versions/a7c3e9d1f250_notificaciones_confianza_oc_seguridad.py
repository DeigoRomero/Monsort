"""Notificaciones, confianza de la OC detectada y columnas de seguridad de usuarios

Revision ID: a7c3e9d1f250
Revises: b8d41f6a2c73
Create Date: 2026-09-30

1. Tabla "Notificaciones": avisos de cada revisión automática (Gmail cada
   5 minutos, verificación SAT) para la campana del frontend.
2. ordenes_compra.confianza_oc: alta | media | baja | ninguna | manual.
   Las OCs existentes quedan en NULL (se detectaron con el parser anterior).
3. Usuarios:
   - intentos_fallidos / bloqueado_hasta: bloqueo temporal tras 5 intentos
     de login fallidos seguidos.
   - sesion_version: se incrementa al cerrar sesión o cambiar contraseña;
     invalida de inmediato los access token emitidos antes.
   - activo: desactivar una cuenta sin borrarla (conserva su historial).
   - ultimo_acceso: auditoría.
   - Se DESACTIVAN las cuentas con un rol que no existe en la aplicación
     (empleado/administrador/desarrollador). En producción eso son dos:
     "Sistema Automatico" (usuario interno del scheduler, nunca debe poder
     iniciar sesión) y "string" / rol "string", una cuenta de prueba creada
     desde /docs con la contraseña "string". No se borran: tienen historial.
   - refresh_token pasa a guardar el SHA-256 del token, no el token. Los
     refresh token vigentes se invalidan (todos vuelven a iniciar sesión una
     vez); guardar el token en claro permitía a quien leyera la tabla
     suplantar a cualquier usuario.
"""
from alembic import op
import sqlalchemy as sa


revision = "a7c3e9d1f250"
down_revision = "b8d41f6a2c73"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "Notificaciones",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("fecha", sa.DateTime(), nullable=False),
        sa.Column("tipo", sa.String(20), nullable=False),
        sa.Column("nivel", sa.String(10), nullable=False, server_default="info"),
        sa.Column("titulo", sa.String(200), nullable=False),
        sa.Column("detalle", sa.Text(), nullable=True),
        sa.Column("seccion", sa.String(20), nullable=True),
    )
    op.create_index("ix_Notificaciones_id", "Notificaciones", ["id"])
    op.create_index("ix_Notificaciones_fecha", "Notificaciones", ["fecha"])

    op.add_column("ordenes_compra", sa.Column("confianza_oc", sa.String(10), nullable=True))

    op.add_column("Usuarios", sa.Column("intentos_fallidos", sa.Integer(),
                                        nullable=False, server_default="0"))
    op.add_column("Usuarios", sa.Column("bloqueado_hasta", sa.DateTime(), nullable=True))
    op.add_column("Usuarios", sa.Column("sesion_version", sa.Integer(),
                                        nullable=False, server_default="0"))
    op.add_column("Usuarios", sa.Column("activo", sa.Boolean(),
                                        nullable=False, server_default=sa.true()))
    op.add_column("Usuarios", sa.Column("ultimo_acceso", sa.DateTime(), nullable=True))
    # Los refresh token en claro dejan de servir: se guardará su hash.
    op.execute('UPDATE "Usuarios" SET refresh_token = NULL, refresh_token_expiracion = NULL')

    bind = op.get_bind()
    desactivadas = bind.execute(sa.text(
        """UPDATE "Usuarios" SET activo = false, sesion_version = sesion_version + 1
           WHERE lower(rol) NOT IN ('empleado', 'administrador', 'desarrollador')
           RETURNING id_usuario, nombre, rol"""
    )).fetchall()
    for fila in desactivadas:
        print(f"    - Cuenta desactivada: #{fila[0]} {fila[1]!r} (rol {fila[2]!r})")


def downgrade() -> None:
    for columna in ("ultimo_acceso", "activo", "sesion_version",
                    "bloqueado_hasta", "intentos_fallidos"):
        op.drop_column("Usuarios", columna)
    op.drop_column("ordenes_compra", "confianza_oc")
    op.drop_index("ix_Notificaciones_fecha", table_name="Notificaciones")
    op.drop_index("ix_Notificaciones_id", table_name="Notificaciones")
    op.drop_table("Notificaciones")
