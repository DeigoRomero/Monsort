# app/services/reporte_service.py

import io
import os
from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_UP

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter, landscape
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import cm
from reportlab.platypus import (
    SimpleDocTemplate, Table, TableStyle, Paragraph,
    Spacer, Image, HRFlowable,
)
from sqlalchemy.orm import Session

from app.modelos.factura import Facturas
from app.modelos.complemento_pago import ComplementosPago
from app.modelos.cp_documento_relacionado import CPDocumentosRelacionados
from app.modelos.conceptos import Conceptos
from app.esquemas.factura import FiltrosFactura
from app.services.query_builder import construir_query_facturas


AZUL_OSCURO = colors.HexColor("#1B2F6E")
AZUL_CLARO  = colors.HexColor("#5B9BD5")
GRIS_FILA   = colors.HexColor("#F2F4F8")
BLANCO      = colors.white

LOGO_PATH = os.path.join(
    os.path.dirname(__file__), "..", "static", "logo_monsort.png"
)


# ─── Helpers ───────────────────────────────────────────────────────────────

def _total_mxn(factura) -> Decimal | None:
    if factura.total is None:
        return Decimal("0")
    if factura.tipo_cambio is None:
        return None   # único caso sin conversión posible
    return (Decimal(str(factura.total)) * Decimal(str(factura.tipo_cambio))).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )


def _fmt_decimal(valor) -> str:
    if valor is None:
        return "—"
    return f"${Decimal(str(valor)):,.2f}"


def _fmt_fecha(valor) -> str:
    if valor is None:
        return "—"
    if isinstance(valor, (date, datetime)):
        return valor.strftime("%d/%m/%Y")
    return str(valor)


def _descripcion_conceptos(conceptos: list) -> str:
    partes = [c.descripcion for c in conceptos if c.descripcion]
    return "; ".join(partes) if partes else "—"


def _tiene_cp_activo(db: Session, id_factura: int) -> bool:
    return (
        db.query(CPDocumentosRelacionados)
        .join(ComplementosPago,
              CPDocumentosRelacionados.id_complemento == ComplementosPago.id)
        .filter(
            CPDocumentosRelacionados.id_factura == id_factura,
            ComplementosPago.cancelado == False,   # noqa: E712
        )
        .first() is not None
    )


# ─── Estilos ───────────────────────────────────────────────────────────────

def _estilos():
    base = getSampleStyleSheet()
    celda = ParagraphStyle(
        "Celda", parent=base["Normal"],
        fontSize=7.5, leading=10, wordWrap="CJK",
    )
    return {
        "titulo": ParagraphStyle(
            "TituloReporte", parent=base["Title"],
            textColor=AZUL_OSCURO, fontSize=14, spaceAfter=4,
        ),
        "subtitulo": ParagraphStyle(
            "Subtitulo", parent=base["Normal"],
            textColor=AZUL_CLARO, fontSize=9, spaceAfter=2,
        ),
        "nota": ParagraphStyle(
            "Nota", parent=base["Normal"],
            textColor=colors.HexColor("#666666"), fontSize=7.5,
            spaceAfter=2, spaceBefore=6,
        ),
        "celda": celda,
        "celda_bold": ParagraphStyle(
            "CeldaBold", parent=celda, fontName="Helvetica-Bold",
        ),
        "seccion": ParagraphStyle(
            "Seccion", parent=base["Heading2"],
            textColor=AZUL_OSCURO, fontSize=10,
            spaceBefore=10, spaceAfter=4,
        ),
    }


# ─── Encabezado ────────────────────────────────────────────────────────────

def _encabezado(story, titulo_texto: str, subtitulo_texto: str, estilos: dict):
    if os.path.exists(LOGO_PATH):
        logo = Image(LOGO_PATH, width=5 * cm, height=2 * cm, kind="proportional")
        story.append(logo)
    story.append(Spacer(1, 0.3 * cm))
    story.append(Paragraph(titulo_texto, estilos["titulo"]))
    story.append(Paragraph(subtitulo_texto, estilos["subtitulo"]))
    story.append(Paragraph(
        f"Generado el {datetime.now().strftime('%d/%m/%Y %H:%M')}",
        estilos["subtitulo"],
    ))
    story.append(HRFlowable(
        width="100%", thickness=1.5, color=AZUL_OSCURO, spaceAfter=8,
    ))


# ─── Estilo de tabla ───────────────────────────────────────────────────────

def _estilo_tabla_base(n_filas: int) -> TableStyle:
    return TableStyle([
        ("BACKGROUND",    (0, 0), (-1, 0),  AZUL_OSCURO),
        ("TEXTCOLOR",     (0, 0), (-1, 0),  BLANCO),
        ("FONTNAME",      (0, 0), (-1, 0),  "Helvetica-Bold"),
        ("FONTSIZE",      (0, 0), (-1, 0),  8),
        ("ALIGN",         (0, 0), (-1, 0),  "CENTER"),
        ("VALIGN",        (0, 0), (-1, -1), "TOP"),
        ("BOTTOMPADDING", (0, 0), (-1, 0),  6),
        ("TOPPADDING",    (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (1, 1), (-1, -1), 4),
        ("GRID",          (0, 0), (-1, -1), 0.4, colors.HexColor("#CCCCCC")),
        ("LINEBELOW",     (0, 0), (-1, 0),  1.5, AZUL_CLARO),
        *[
            ("BACKGROUND", (0, i), (-1, i), GRIS_FILA)
            for i in range(2, n_filas, 2)
        ],
    ])


# ─── Tabla Campo / Valor ───────────────────────────────────────────────────

def _tabla_dos_columnas(datos: list, estilos: dict) -> Table:
    filas = []
    for i, fila in enumerate(datos):
        estilo_val = estilos["celda_bold"] if i == 0 else estilos["celda"]
        filas.append([
            Paragraph(str(fila[0]), estilos["celda_bold"]),
            Paragraph(str(fila[1]), estilo_val),
        ])
    n = len(filas)
    tabla = Table(filas, colWidths=[5 * cm, 11 * cm])
    estilo = _estilo_tabla_base(n)
    estilo.add("BACKGROUND", (0, 1), (0, -1), colors.HexColor("#E8EDF5"))
    tabla.setStyle(estilo)
    return tabla


# ═══════════════════════════════════════════════════════════════════════════
# REPORTE GENERAL
# ═══════════════════════════════════════════════════════════════════════════

def generar_reporte_general(db: Session, filtros: FiltrosFactura) -> bytes:
    filtros.incluir_canceladas = False

    facturas_raw = (
        construir_query_facturas(db, filtros)
        .order_by(Facturas.fecha.asc())
        .all()
    )

    # Solo ciclo completo: OC vinculada + CP activo
    facturas = [
        f for f in facturas_raw
        if f.id_orden_compra is not None and _tiene_cp_activo(db, f.id_factura)
    ]

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=landscape(letter),
        leftMargin=1.5*cm, rightMargin=1.5*cm,
        topMargin=1.5*cm,  bottomMargin=1.5*cm,
    )

    estilos = _estilos()
    story   = []

    partes = []
    if filtros.cliente:
        partes.append(f"Cliente: {filtros.cliente}")
    if filtros.fecha_desde or filtros.fecha_hasta:
        partes.append(
            f"Período: {_fmt_fecha(filtros.fecha_desde)} — {_fmt_fecha(filtros.fecha_hasta)}"
        )
    if filtros.numero_oc:
        partes.append(f"OC: {filtros.numero_oc}")
    subtitulo_txt = " | ".join(partes) if partes else "Todas las facturas"

    _encabezado(story, "Reporte General de Facturas", subtitulo_txt, estilos)

    if not facturas:
        story.append(Paragraph(
            "No se encontraron facturas con ciclo completo para los filtros aplicados.",
            estilos["celda"],
        ))
        doc.build(story)
        return buffer.getvalue()

    # Anchos landscape (~27.9 cm útiles)
    col_widths = [3*cm, 2.5*cm, 2.5*cm, 6.7*cm, 2.5*cm, 2.5*cm, 2.8*cm, 1.8*cm, 2.2*cm]

    encabezado_tabla = [[
        Paragraph("UUID / Folio Fiscal",  estilos["celda_bold"]),
        Paragraph("Folio Interno",        estilos["celda_bold"]),
        Paragraph("Orden de Compra",      estilos["celda_bold"]),
        Paragraph("Descripción",          estilos["celda_bold"]),
        Paragraph("IVA",                  estilos["celda_bold"]),
        Paragraph("Subtotal",             estilos["celda_bold"]),
        Paragraph("Total MXN",            estilos["celda_bold"]),
        Paragraph("Moneda",               estilos["celda_bold"]),
        Paragraph("Fecha",                estilos["celda_bold"]),
    ]]

    filas          = []
    suma_total_mxn = Decimal("0")
    hay_sin_conv   = False  # flag para nota al pie

    for f in facturas:
        conceptos    = db.query(Conceptos).filter(Conceptos.id_factura == f.id_factura).all()
        descripcion  = _descripcion_conceptos(conceptos)
        total_mxn    = _total_mxn(f)

        if total_mxn is not None:
            suma_total_mxn += total_mxn
            celda_total = Paragraph(_fmt_decimal(total_mxn), estilos["celda"])
        else:
            # Moneda extranjera sin tipo de cambio
            hay_sin_conv = True
            celda_total  = Paragraph(
                f"{_fmt_decimal(f.total)} ({f.moneda or '?'})*",
                estilos["celda"],
            )

        filas.append([
            Paragraph(f.folio_fiscal  or "—", estilos["celda"]),
            Paragraph(f.folio_interno or "—", estilos["celda"]),
            Paragraph(f.numero_oc     or "—", estilos["celda"]),
            Paragraph(descripcion,             estilos["celda"]),
            Paragraph(_fmt_decimal(f.iva),     estilos["celda"]),
            Paragraph(_fmt_decimal(f.subtotal),estilos["celda"]),
            celda_total,
            Paragraph(f.moneda or "MXN",       estilos["celda"]),
            Paragraph(_fmt_fecha(f.fecha),     estilos["celda"]),
        ])

    # Fila de total
    nota_total = "" if not hay_sin_conv else " (excluye facturas marcadas con *)"
    fila_total = [
        Paragraph(f"TOTAL  ({len(facturas)} facturas){nota_total}", estilos["celda_bold"]),
        Paragraph("", estilos["celda"]),
        Paragraph("", estilos["celda"]),
        Paragraph("", estilos["celda"]),
        Paragraph("", estilos["celda"]),
        Paragraph("", estilos["celda"]),
        Paragraph(_fmt_decimal(suma_total_mxn), estilos["celda_bold"]),
        Paragraph("MXN",                        estilos["celda_bold"]),
        Paragraph("", estilos["celda"]),
    ]

    datos_tabla = encabezado_tabla + filas + [fila_total]
    n = len(datos_tabla)

    tabla  = Table(datos_tabla, colWidths=col_widths, repeatRows=1)
    estilo = _estilo_tabla_base(n)
    estilo.add("BACKGROUND", (0, n-1), (-1, n-1), AZUL_CLARO)
    estilo.add("TEXTCOLOR",  (0, n-1), (-1, n-1), BLANCO)
    estilo.add("FONTNAME",   (0, n-1), (-1, n-1), "Helvetica-Bold")
    estilo.add("LINEABOVE",  (0, n-1), (-1, n-1), 1.5, AZUL_OSCURO)
    tabla.setStyle(estilo)
    story.append(tabla)

    # Nota al pie si hay facturas sin tipo de cambio
    if hay_sin_conv:
        story.append(Spacer(1, 0.3*cm))
        story.append(Paragraph(
            "* Factura en moneda extranjera sin tipo de cambio registrado. "
            "El monto se muestra en su moneda original y no se incluye en el total MXN. "
            "Capture el tipo de cambio desde el dashboard para incluirla en futuros reportes.",
            estilos["nota"],
        ))

    doc.build(story)
    return buffer.getvalue()


# ═══════════════════════════════════════════════════════════════════════════
# REPORTE DETALLE
# ═══════════════════════════════════════════════════════════════════════════

def generar_reporte_detalle(db: Session, id_factura: int) -> bytes:
    f = db.query(Facturas).filter(Facturas.id_factura == id_factura).first()
    if not f:
        raise ValueError(f"Factura {id_factura} no encontrada")

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=letter,
        leftMargin=2*cm, rightMargin=2*cm,
        topMargin=1.5*cm, bottomMargin=1.5*cm,
    )

    estilos = _estilos()
    story   = []

    _encabezado(
        story,
        "Reporte Detallado de Factura",
        f"Folio Fiscal: {f.folio_fiscal}  |  Cliente: {f.cliente}",
        estilos,
    )

    # ── Sección 1: Factura ──────────────────────────────────────────────
    story.append(Paragraph("1. Datos de la Factura", estilos["seccion"]))

    conceptos   = db.query(Conceptos).filter(Conceptos.id_factura == id_factura).all()
    descripcion = _descripcion_conceptos(conceptos)
    total_mxn   = _total_mxn(f)

    total_mxn_txt = (
        _fmt_decimal(total_mxn)
        if total_mxn is not None
        else f"{_fmt_decimal(f.total)} ({f.moneda}) — sin tipo de cambio registrado"
    )

    datos_factura = [
        ["Campo",             "Valor"],
        ["UUID / Folio Fiscal", f.folio_fiscal or "—"],
        ["Folio Interno",     f.folio_interno or "—"],
        ["RFC Receptor",      f.rfc or "—"],
        ["Cliente",           f.cliente or "—"],
        ["Fecha Emisión",     _fmt_fecha(f.fecha)],
        ["Fecha Validación",  _fmt_fecha(f.fecha_validacion)],
        ["Orden de Compra",   f.numero_oc or "—"],
        ["Descripción",       descripcion],
        ["Moneda",            f.moneda or "MXN"],
        ["Tipo de Cambio",    str(f.tipo_cambio) if f.tipo_cambio else "No registrado"],
        ["Subtotal",          _fmt_decimal(f.subtotal)],
        ["IVA",               _fmt_decimal(f.iva)],
        ["Total",             _fmt_decimal(f.total)],
        ["Total MXN",         total_mxn_txt],
        ["Estado",            f.estado.nombre_estado if f.estado else "—"],
        ["Fecha Liquidación", _fmt_fecha(f.fecha_liquidacion)],
    ]

    story.append(_tabla_dos_columnas(datos_factura, estilos))
    story.append(Spacer(1, 0.5*cm))

    # ── Sección 2: Orden de Compra ──────────────────────────────────────
    story.append(Paragraph("2. Orden de Compra", estilos["seccion"]))

    if f.orden_compra:
        oc = f.orden_compra
        datos_oc = [
            ["Campo",               "Valor"],
            ["Número OC",           oc.numero_oc or "—"],
            ["Número OC Detectado", oc.numero_oc_detectado or "—"],
            ["Nombre de Archivo",   oc.nombre_archivo or "—"],
            ["Fecha Recepción",     _fmt_fecha(oc.fecha_recepcion)],
            ["Archivo",             "Disponible" if oc.archivo else "No disponible"],
        ]
    else:
        datos_oc = [
            ["Campo",  "Valor"],
            ["Estado", "Sin orden de compra vinculada"],
        ]

    story.append(_tabla_dos_columnas(datos_oc, estilos))
    story.append(Spacer(1, 0.5*cm))

    # ── Sección 3: Complementos de Pago ────────────────────────────────
    story.append(Paragraph("3. Complemento(s) de Pago", estilos["seccion"]))

    vinculos_cp = (
        db.query(CPDocumentosRelacionados)
        .join(ComplementosPago,
              CPDocumentosRelacionados.id_complemento == ComplementosPago.id)
        .filter(
            CPDocumentosRelacionados.id_factura == id_factura,
            ComplementosPago.cancelado == False,   # noqa: E712
        )
        .all()
    )

    if not vinculos_cp:
        story.append(Paragraph("Sin complementos de pago activos.", estilos["celda"]))
    else:
        for i, vinculo in enumerate(vinculos_cp, start=1):
            cp = db.query(ComplementosPago).filter(
                ComplementosPago.id == vinculo.id_complemento
            ).first()
            if not cp:
                continue

            story.append(Paragraph(f"CP {i}: {cp.uuid_cp}", estilos["celda_bold"]))
            datos_cp_tabla = [
                ["Campo",             "Valor"],
                ["UUID CP",           cp.uuid_cp or "—"],
                ["Folio CP",          cp.folio or "—"],
                ["Fecha de Pago",     _fmt_fecha(cp.fecha_pago)],
                ["Moneda",            cp.moneda or "—"],
                ["Tipo de Cambio CP", str(cp.tipo_cambio) if cp.tipo_cambio else "No registrado"],
                ["Monto CP",          _fmt_decimal(cp.monto)],
                ["Forma de Pago",     cp.forma_pago or "—"],
                ["Importe Pagado",    _fmt_decimal(vinculo.imp_pagado)],
                ["Saldo Insoluto",    _fmt_decimal(vinculo.imp_saldo_insoluto)],
                ["Parcialidad",       str(vinculo.num_parcialidad) if vinculo.num_parcialidad else "—"],
            ]
            story.append(_tabla_dos_columnas(datos_cp_tabla, estilos))
            story.append(Spacer(1, 0.3*cm))

    doc.build(story)
    return buffer.getvalue()