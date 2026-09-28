"""Sincronizacion con el portal del SAT: XML en FacturasRecibidas y bitacora

Revision ID: b8d41f6a2c73
Revises: f3b6c1d84e92
Create Date: 2026-09-28

Por que existe
--------------
La Descarga Masiva del SAT tarda dias en entregar (cuando entrega). Para ver
las recibidas "al dia" se consulta el portal de CFDI con la e.firma varias
veces al dia (app/services/portal_sat_service.py + Backend/sat_portal/).

Esta migracion agrega lo que esa sincronizacion necesita guardar:

  FacturasRecibidas.xml_factura            el XML del CFDI. La metadata del SAT
                                           no lo trae; el portal si.
  FacturasRecibidas.fecha_vista_portal     ultima vez que el portal la listo.
  SincronizacionesPortal                   una fila por corrida: cuando, que
                                           rango, cuantas nuevas, y el error si
                                           fallo. Es lo que el frontend usa para
                                           "Ultima sincronizacion: hoy 13:02".

Idempotente: revisa el catalogo antes de crear cada cosa (leccion de
bce9ea1ab852, que se marco con stamp sin ejecutar su DDL).
"""
import sqlalchemy as sa
from alembic import op

revision = "b8d41f6a2c73"
down_revision = "f3b6c1d84e92"
branch_labels = None
depends_on = None

TABLA_BITACORA = "SincronizacionesPortal"


def upgrade() -> None:
    conexion = op.get_bind()
    inspector = sa.inspect(conexion)

    columnas = {c["name"] for c in inspector.get_columns("FacturasRecibidas")}

    if "xml_factura" not in columnas:
        op.add_column(
            "FacturasRecibidas",
            sa.Column("xml_factura", sa.LargeBinary(), nullable=True),
        )

    if "fecha_vista_portal" not in columnas:
        op.add_column(
            "FacturasRecibidas",
            sa.Column("fecha_vista_portal", sa.DateTime(timezone=True), nullable=True),
        )

    if TABLA_BITACORA not in inspector.get_table_names():
        op.create_table(
            TABLA_BITACORA,
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("tipo_comprobante", sa.String(length=20), nullable=False,
                      server_default="recibidos"),
            sa.Column("fecha_desde", sa.Date(), nullable=False),
            sa.Column("fecha_hasta", sa.Date(), nullable=False),
            # programada | barrido | manual | cli
            sa.Column("motivo", sa.String(length=20), nullable=False),
            # EN_CURSO | EXITOSA | FALLIDA
            sa.Column("estado", sa.String(length=20), nullable=False),
            sa.Column("inicio", sa.DateTime(timezone=True), nullable=False),
            sa.Column("fin", sa.DateTime(timezone=True), nullable=True),
            sa.Column("cfdis_encontrados", sa.Integer(), nullable=True),
            sa.Column("nuevas", sa.Integer(), nullable=True),
            sa.Column("actualizadas", sa.Integer(), nullable=True),
            sa.Column("xml_descargados", sa.Integer(), nullable=True),
            sa.Column("rechazadas", sa.Integer(), nullable=True),
            # configuracion | credencial | login | portal | timeout | interno
            sa.Column("tipo_error", sa.String(length=30), nullable=True),
            sa.Column("error", sa.Text(), nullable=True),
            sa.Column("avisos", sa.Text(), nullable=True),
        )
        op.create_index(
            "ix_sincronizaciones_portal_inicio", TABLA_BITACORA, ["inicio"]
        )


def downgrade() -> None:
    conexion = op.get_bind()
    inspector = sa.inspect(conexion)

    if TABLA_BITACORA in inspector.get_table_names():
        op.drop_index("ix_sincronizaciones_portal_inicio", table_name=TABLA_BITACORA)
        op.drop_table(TABLA_BITACORA)

    columnas = {c["name"] for c in inspector.get_columns("FacturasRecibidas")}
    # OJO: esto borra los XML descargados. Se pueden volver a bajar del portal.
    if "fecha_vista_portal" in columnas:
        op.drop_column("FacturasRecibidas", "fecha_vista_portal")
    if "xml_factura" in columnas:
        op.drop_column("FacturasRecibidas", "xml_factura")
