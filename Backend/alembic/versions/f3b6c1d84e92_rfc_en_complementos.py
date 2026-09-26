"""RFC de emisor y receptor en ComplementosPago, con relleno desde el XML

Revision ID: f3b6c1d84e92
Revises: e5a3c17b9d24
Create Date: 2026-09-26

Por que existe
--------------
procesar_complemento_pago() guardaba cualquier CP que llegara al buzon. Las
facturas si se filtraban por RFC emisor desde siempre (procesar_factura), los
CP no. Resultado: complementos de otras empresas — correo en copia o reenviado
— guardados como propios, y sus DoctoRelacionado apuntando a facturas de un
tercero que nunca van a estar en Facturas. En produccion aparecieron asi 3 de
los 10 pagos "sin factura": el CP con folio 156, que el cliente identifico como
ajeno.

Esta migracion agrega las dos columnas y las rellena leyendo el XML que ya
estaba guardado en archivo_xml, asi que no hace falta reprocesar correos.

Deliberado: NO borra los CP ajenos que ya estan en la base. Borrar documentos
fiscales en una migracion, en silencio y sin vuelta atras, no es una decision
que deba tomar un upgrade. Despues de esta migracion se pueden listar con:

    SELECT folio, uuid_cp, rfc_emisor, rfc_receptor
    FROM "ComplementosPago"
    WHERE rfc_emisor IS NOT NULL AND rfc_receptor IS NOT NULL
      AND 'MSF140227BF7' NOT IN (rfc_emisor, rfc_receptor);

y borrarse a mano una vez revisados.
"""
import logging
import re

import sqlalchemy as sa
from alembic import op

revision = "f3b6c1d84e92"
down_revision = "e5a3c17b9d24"
branch_labels = None
depends_on = None

logger = logging.getLogger("alembic.runtime.migration")

# Se lee con expresion regular y no con un parser XML a proposito: aqui solo
# interesan dos atributos, el XML puede venir con cualquier prefijo de
# namespace (cfdi:, c:, ninguno) y un documento mal formado no debe tumbar la
# migracion entera. [^>]* no cruza el cierre de la etiqueta, asi que el Rfc
# capturado es el de esa etiqueta y no el de otra.
PATRON_EMISOR = re.compile(rb'<[^>]*Emisor[^>]*\sRfc="([^"]*)"', re.IGNORECASE)
PATRON_RECEPTOR = re.compile(rb'<[^>]*Receptor[^>]*\sRfc="([^"]*)"', re.IGNORECASE)

LARGO_RFC = 13


def _rfc(coincidencia) -> str | None:
    if not coincidencia:
        return None
    valor = coincidencia.group(1).decode("ascii", errors="ignore").strip().upper()
    # Un RFC valido tiene 12 (moral) o 13 (fisica) caracteres. Si lo que salio
    # no cabe en la columna, es que el regex agarro otra cosa: mejor NULL.
    return valor if 0 < len(valor) <= LARGO_RFC else None


def upgrade() -> None:
    conexion = op.get_bind()
    columnas = {
        c["name"] for c in sa.inspect(conexion).get_columns("ComplementosPago")
    }

    if "rfc_emisor" not in columnas:
        op.add_column(
            "ComplementosPago",
            sa.Column("rfc_emisor", sa.String(length=LARGO_RFC), nullable=True),
        )
        op.create_index(
            "ix_complementos_rfc_emisor", "ComplementosPago", ["rfc_emisor"]
        )

    if "rfc_receptor" not in columnas:
        op.add_column(
            "ComplementosPago",
            sa.Column("rfc_receptor", sa.String(length=LARGO_RFC), nullable=True),
        )

    # --- Relleno desde el XML guardado ---
    filas = conexion.execute(sa.text("""
        SELECT id, archivo_xml
        FROM "ComplementosPago"
        WHERE archivo_xml IS NOT NULL
          AND (rfc_emisor IS NULL OR rfc_receptor IS NULL)
    """)).fetchall()

    actualizados = 0
    sin_leer = 0
    for id_cp, xml in filas:
        if xml is None:
            continue
        datos = bytes(xml)
        emisor = _rfc(PATRON_EMISOR.search(datos))
        receptor = _rfc(PATRON_RECEPTOR.search(datos))
        if not emisor and not receptor:
            sin_leer += 1
            continue
        conexion.execute(
            sa.text("""
                UPDATE "ComplementosPago"
                SET rfc_emisor = COALESCE(:emisor, rfc_emisor),
                    rfc_receptor = COALESCE(:receptor, rfc_receptor)
                WHERE id = :id
            """),
            {"emisor": emisor, "receptor": receptor, "id": id_cp},
        )
        actualizados += 1

    logger.info(
        "ComplementosPago: RFC rellenados en %d de %d CP con XML (%d sin RFC legible)",
        actualizados, len(filas), sin_leer,
    )


def downgrade() -> None:
    conexion = op.get_bind()
    inspector = sa.inspect(conexion)
    columnas = {c["name"] for c in inspector.get_columns("ComplementosPago")}
    indices = {i["name"] for i in inspector.get_indexes("ComplementosPago")}

    if "ix_complementos_rfc_emisor" in indices:
        op.drop_index("ix_complementos_rfc_emisor", table_name="ComplementosPago")
    if "rfc_receptor" in columnas:
        op.drop_column("ComplementosPago", "rfc_receptor")
    if "rfc_emisor" in columnas:
        op.drop_column("ComplementosPago", "rfc_emisor")
