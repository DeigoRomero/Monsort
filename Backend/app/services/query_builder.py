# Función central que todos los endpoints comparten:
#   - GET /facturas/          (listado paginado)
#   - GET /facturas/resumen   (totales)
#   - POST /reportes/general  (PDF filtrado)
#   - POST /reportes/detalle/{id} (PDF por factura)
#
# Ningún consumidor reimplementa los filtros: todos llaman a
# construir_query_facturas() y agregan lo suyo al final.

from sqlalchemy.orm import Session, load_only
from sqlalchemy import func, case, and_, or_
from decimal import Decimal
from datetime import date

from app.modelos.factura import Facturas
from app.modelos.estados import Estados
from app.modelos.cp_documento_relacionado import CPDocumentosRelacionados
from app.modelos.complemento_pago import ComplementosPago
from app.esquemas.factura import FiltrosFactura, ResumenFacturas


def construir_query_facturas(db: Session, filtros: FiltrosFactura):
    """
    Devuelve un Query de SQLAlchemy sin ejecutar.
    Nunca carga binarios (pdf, xml, oc): usa load_only.

    Cada consumidor agrega al final:
      - Listado:  .offset(...).limit(...).all()
      - Resumen:  se pasa a calcular_resumen()
      - Reporte:  .all()
    """
    COLUMNAS = [
        Facturas.id_factura,
        Facturas.folio_fiscal,
        Facturas.folio_interno,
        Facturas.cliente,
        Facturas.rfc,
        Facturas.fecha,
        Facturas.numero_oc,
        Facturas.moneda,
        Facturas.tipo_cambio,
        Facturas.subtotal,
        Facturas.iva,
        Facturas.total,
        Facturas.fecha_liquidacion,
        Facturas.fecha_validacion,
        Facturas.id_estado,
        Facturas.id_orden_compra,
    ]

    q = db.query(Facturas).options(load_only(*COLUMNAS))

    # --- Canceladas ---
    if not filtros.incluir_canceladas:
        q = q.join(Estados, Facturas.id_estado == Estados.id_estado).filter(
            Estados.nombre_estado != "Cancelada"
        )

    # --- Búsqueda libre (OR sobre 4 campos) ---
    if filtros.q:
        termino = f"%{filtros.q}%"
        q = q.filter(
            or_(
                Facturas.cliente.ilike(termino),
                Facturas.folio_fiscal.ilike(termino),
                Facturas.folio_interno.ilike(termino),
                Facturas.numero_oc.ilike(termino),
            )
        )

    # --- Filtro por cliente ---
    if filtros.cliente:
        q = q.filter(Facturas.cliente.ilike(f"%{filtros.cliente}%"))

    # --- Filtro por OC ---
    if filtros.numero_oc:
        q = q.filter(Facturas.numero_oc.ilike(f"%{filtros.numero_oc}%"))

    # --- Rango de fechas (sobre fecha_emision del CFDI) ---
    if filtros.fecha_desde:
        q = q.filter(Facturas.fecha >= filtros.fecha_desde)
    if filtros.fecha_hasta:
        q = q.filter(Facturas.fecha <= filtros.fecha_hasta)

    # --- Con / sin CP ---
    if filtros.con_cp is not None:
        subquery_cp = (
            db.query(CPDocumentosRelacionados.id_factura)
            .join(ComplementosPago,
                  CPDocumentosRelacionados.id_complemento == ComplementosPago.id)
            .filter(ComplementosPago.cancelado == False)   # noqa: E712
            .distinct()
            .subquery()
        )
        if filtros.con_cp:
            q = q.filter(Facturas.id_factura.in_(subquery_cp))
        else:
            q = q.filter(Facturas.id_factura.notin_(subquery_cp))

    return q


def calcular_resumen(db: Session, filtros: FiltrosFactura) -> ResumenFacturas:
    """
    Corre una sola consulta de agregación sobre el mismo criterio
    que construir_query_facturas(). No trae filas a Python.
    """
    q_base = construir_query_facturas(db, filtros)

    # Subquery: ids de facturas con CP activo vinculado
    subquery_cp = (
        db.query(CPDocumentosRelacionados.id_factura)
        .join(ComplementosPago,
              CPDocumentosRelacionados.id_complemento == ComplementosPago.id)
        .filter(ComplementosPago.cancelado == False)       # noqa: E712
        .distinct()
        .subquery()
    )

    resultado = q_base.with_entities(
        func.count(Facturas.id_factura).label("total_facturas"),

        # Total en MXN: si moneda es MXN usa total directo,
        # si no multiplica por tipo_cambio.
        func.coalesce(
            func.sum(
                case(
                    (
                        or_(Facturas.moneda == "MXN", Facturas.tipo_cambio.is_(None)),
                        Facturas.total
                    ),
                    else_=Facturas.total * Facturas.tipo_cambio
                )
            ),
            Decimal("0")
        ).label("total_mxn"),

        # Facturas con CP
        func.count(
            case((Facturas.id_factura.in_(subquery_cp), Facturas.id_factura))
        ).label("total_con_cp"),
    ).one()

    total_facturas = resultado.total_facturas or 0
    total_con_cp = resultado.total_con_cp or 0
    total_mxn = resultado.total_mxn or Decimal("0")

    # Canceladas: solo si el filtro las incluye, sino es 0
    total_canceladas = 0
    if filtros.incluir_canceladas:
        total_canceladas = (
            db.query(func.count(Facturas.id_factura))
            .join(Estados, Facturas.id_estado == Estados.id_estado)
            .filter(Estados.nombre_estado == "Cancelada")
            .scalar() or 0
        )

    return ResumenFacturas(
        total_facturas=total_facturas,
        total_mxn=total_mxn,
        total_con_cp=total_con_cp,
        total_sin_cp=total_facturas - total_con_cp,
        total_canceladas=total_canceladas,
    )