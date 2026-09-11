# app/services/reporte_recibidas_service.py

import io
from datetime import datetime
from decimal import Decimal

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter, landscape
from reportlab.lib.units import cm
from reportlab.platypus import SimpleDocTemplate, Table, Paragraph, Spacer
from sqlalchemy.orm import Session

from app.modelos.facturas_recibidas import FacturasRecibidas
from app.esquemas.factura_recibida import FiltrosFacturaRecibida
from app.services.query_builder_recibidas import construir_query_facturas_recibidas

# Se reutilizan los helpers y estilos del reporte de emitidas: un solo
# lugar define el logo, los colores y el formato de montos y fechas.
# Si el look cambia, cambia en los dos reportes a la vez.
from app.services.reporte_service import (
    AZUL_OSCURO,
    AZUL_CLARO,
    BLANCO,
    _estilos,
    _encabezado,
    _estilo_tabla_base,
    _fmt_decimal,
    _fmt_fecha,
)

VERDE_OSCURO = colors.HexColor("#2E7D32")
ROJO_OSCURO = colors.HexColor("#C62828")
GRIS_GRUPO = colors.HexColor("#E8EDF5")

# Ancho util en landscape letter con margenes de 1.5 cm: ~24.4 cm
ANCHOS = [7.0 * cm, 2.8 * cm, 1.8 * cm, 3.4 * cm, 2.6 * cm, 3.0 * cm]

ETIQUETA_EFECTO = {
    "I": "Ingreso",
    "E": "Egreso",
    "T": "Traslado",
    "N": "Nomina",
    "P": "Pago",
}


def _agrupar_por_emisor(facturas: list) -> dict:
    """
    {rfc_emisor: {"nombre": str, "facturas": [...]}}

    Se agrupa en Python y no en SQL porque de todas formas hace falta
    recorrer las filas para armar la tabla, y asi el orden dentro de
    cada grupo queda controlado aqui.
    """
    grupos: dict[str, dict] = {}
    for f in facturas:
        clave = f.rfc_emisor
        if clave not in grupos:
            grupos[clave] = {"nombre": f.nombre_emisor or clave, "facturas": []}
        # El nombre puede venir vacio en unos registros y lleno en otros:
        # se conserva el primero que traiga algo.
        if not grupos[clave]["nombre"] or grupos[clave]["nombre"] == clave:
            if f.nombre_emisor:
                grupos[clave]["nombre"] = f.nombre_emisor
        grupos[clave]["facturas"].append(f)
    return grupos


def _subtitulo(filtros: FiltrosFacturaRecibida) -> str:
    partes = []
    if filtros.rfc_emisor or filtros.nombre_emisor:
        partes.append(f"Proveedor: {filtros.nombre_emisor or filtros.rfc_emisor}")
    if filtros.fecha_desde or filtros.fecha_hasta:
        partes.append(
            f"Periodo: {_fmt_fecha(filtros.fecha_desde)} — {_fmt_fecha(filtros.fecha_hasta)}"
        )
    if filtros.sat_estado:
        partes.append(f"Estatus: {filtros.sat_estado}")
    if filtros.efecto_comprobante:
        partes.append(
            f"Tipo: {ETIQUETA_EFECTO.get(filtros.efecto_comprobante, filtros.efecto_comprobante)}"
        )
    elif filtros.solo_facturas:
        partes.append("Solo comprobantes de ingreso")
    return " | ".join(partes) if partes else "Todas las facturas recibidas"


def generar_reporte_recibidas(db: Session, filtros: FiltrosFacturaRecibida) -> bytes:
    """
    Reporte de CFDI recibidos agrupado por proveedor, con subtotal por
    proveedor y total general.

    Las canceladas SI aparecen (el cliente pidio ver el estatus) pero NO
    suman a los totales: un comprobante cancelado no representa un gasto.
    """
    facturas = (
        construir_query_facturas_recibidas(db, filtros)
        .order_by(
            FacturasRecibidas.rfc_emisor.asc(),
            FacturasRecibidas.fecha_emision.asc(),
        )
        .all()
    )

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=landscape(letter),
        leftMargin=1.5 * cm, rightMargin=1.5 * cm,
        topMargin=1.5 * cm, bottomMargin=1.5 * cm,
    )

    estilos = _estilos()
    story = []

    _encabezado(
        story,
        "Reporte de Facturas Recibidas",
        _subtitulo(filtros),
        estilos,
    )

    if not facturas:
        story.append(Paragraph(
            "No se encontraron facturas recibidas con los filtros aplicados.",
            estilos["celda"],
        ))
        doc.build(story)
        return buffer.getvalue()

    grupos = _agrupar_por_emisor(facturas)

    encabezado_tabla = [[
        Paragraph("Folio Fiscal", estilos["celda_bold"]),
        Paragraph("Fecha Emision", estilos["celda_bold"]),
        Paragraph("Tipo", estilos["celda_bold"]),
        Paragraph("Monto", estilos["celda_bold"]),
        Paragraph("Estatus", estilos["celda_bold"]),
        Paragraph("Fecha Cancelacion", estilos["celda_bold"]),
    ]]

    total_general = Decimal("0")
    total_vigentes = 0
    total_canceladas = 0

    for rfc, datos in sorted(grupos.items(), key=lambda x: x[1]["nombre"]):
        story.append(Spacer(1, 0.3 * cm))
        story.append(Paragraph(
            f"{datos['nombre']}  —  {rfc}",
            estilos["seccion"],
        ))

        filas = []
        subtotal = Decimal("0")
        canceladas_grupo = 0
        indices_canceladas = []

        for f in datos["facturas"]:
            es_cancelada = f.sat_estado == "Cancelado"

            if es_cancelada:
                canceladas_grupo += 1
                total_canceladas += 1
                # +1 por la fila de encabezado
                indices_canceladas.append(len(filas) + 1)
            else:
                subtotal += Decimal(str(f.monto_total or 0))
                total_vigentes += 1

            filas.append([
                Paragraph(f.folio_fiscal or "—", estilos["celda"]),
                Paragraph(_fmt_fecha(f.fecha_emision), estilos["celda"]),
                Paragraph(
                    ETIQUETA_EFECTO.get(f.efecto_comprobante, f.efecto_comprobante or "—"),
                    estilos["celda"],
                ),
                Paragraph(_fmt_decimal(f.monto_total), estilos["celda"]),
                Paragraph(f.sat_estado or "—", estilos["celda"]),
                Paragraph(_fmt_fecha(f.fecha_cancelacion), estilos["celda"]),
            ])

        total_general += subtotal

        etiqueta = f"Subtotal  ({len(datos['facturas'])} comprobantes"
        if canceladas_grupo:
            etiqueta += f", {canceladas_grupo} cancelado(s) no sumado(s)"
        etiqueta += ")"

        filas.append([
            Paragraph(etiqueta, estilos["celda_bold"]),
            Paragraph("", estilos["celda"]),
            Paragraph("", estilos["celda"]),
            Paragraph(_fmt_decimal(subtotal), estilos["celda_bold"]),
            Paragraph("", estilos["celda"]),
            Paragraph("", estilos["celda"]),
        ])

        datos_tabla = encabezado_tabla + filas
        n = len(datos_tabla)

        tabla = Table(datos_tabla, colWidths=ANCHOS, repeatRows=1)
        estilo = _estilo_tabla_base(n)

        # Fila de subtotal
        estilo.add("BACKGROUND", (0, n - 1), (-1, n - 1), GRIS_GRUPO)
        estilo.add("LINEABOVE", (0, n - 1), (-1, n - 1), 1.0, AZUL_CLARO)

        # Canceladas en rojo, para que salten a la vista
        for i in indices_canceladas:
            estilo.add("TEXTCOLOR", (0, i), (-1, i), ROJO_OSCURO)

        tabla.setStyle(estilo)
        story.append(tabla)

    # ── Total general ──────────────────────────────────────────────────
    story.append(Spacer(1, 0.5 * cm))

    fila_total = [[
        Paragraph(
            f"TOTAL GENERAL  —  {len(grupos)} proveedor(es), "
            f"{total_vigentes} comprobante(s) vigente(s)",
            estilos["celda_bold"],
        ),
        Paragraph(_fmt_decimal(total_general), estilos["celda_bold"]),
    ]]

    tabla_total = Table(fila_total, colWidths=[18.0 * cm, 6.6 * cm])
    estilo_total = _estilo_tabla_base(1)
    estilo_total.add("BACKGROUND", (0, 0), (-1, 0), AZUL_CLARO)
    estilo_total.add("TEXTCOLOR", (0, 0), (-1, 0), BLANCO)
    estilo_total.add("LINEABOVE", (0, 0), (-1, 0), 1.5, AZUL_OSCURO)
    estilo_total.add("ALIGN", (0, 0), (-1, 0), "LEFT")
    tabla_total.setStyle(estilo_total)
    story.append(tabla_total)

    # ── Notas al pie ───────────────────────────────────────────────────
    story.append(Spacer(1, 0.3 * cm))

    story.append(Paragraph(
        "Los montos provienen de la Metadata de Descarga Masiva del SAT, que "
        "NO incluye el campo de moneda. Los importes se muestran y suman tal "
        "como los reporta el SAT, sin conversion. Si algun proveedor factura "
        "en moneda extranjera, el total no es directamente comparable.",
        estilos["nota"],
    ))

    if total_canceladas:
        story.append(Paragraph(
            f"Se encontraron {total_canceladas} comprobante(s) cancelado(s) "
            "ante el SAT (marcados en rojo). Aparecen en el detalle para su "
            "revision, pero no se incluyen en los subtotales ni en el total "
            "general.",
            estilos["nota"],
        ))

    doc.build(story)
    return buffer.getvalue()