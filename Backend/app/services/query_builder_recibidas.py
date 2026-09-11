r"""
Construccion de queries sobre FacturasRecibidas.

Fuente UNICA de verdad del filtrado: el listado, el resumen y el reporte
PDF consumen la misma funcion. Si el filtrado viviera duplicado en cada
endpoint, el PDF y la tabla acabarian mostrando conjuntos distintos.

Ubicacion sugerida: app/services/query_builder_recibidas.py
"""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.modelos.facturas_recibidas import FacturasRecibidas
from app.esquemas.factura_recibida import (
    FiltrosFacturaRecibida,
    ResumenFacturasRecibidas,
)

EFECTO_INGRESO = "I"


def construir_query_facturas_recibidas(
    db: Session,
    filtros: FiltrosFacturaRecibida,
):
    """Devuelve un Query sin ordenar ni paginar."""
    q = db.query(FacturasRecibidas)

    # --- busqueda libre ---
    if filtros.q:
        patron = f"%{filtros.q.strip()}%"
        q = q.filter(
            or_(
                FacturasRecibidas.folio_fiscal.ilike(patron),
                FacturasRecibidas.rfc_emisor.ilike(patron),
                FacturasRecibidas.nombre_emisor.ilike(patron),
            )
        )

    # --- emisor ---
    if filtros.rfc_emisor:
        q = q.filter(FacturasRecibidas.rfc_emisor.ilike(f"%{filtros.rfc_emisor}%"))

    if filtros.nombre_emisor:
        q = q.filter(
            FacturasRecibidas.nombre_emisor.ilike(f"%{filtros.nombre_emisor}%")
        )

    # --- estatus ---
    if filtros.sat_estado:
        q = q.filter(FacturasRecibidas.sat_estado == filtros.sat_estado)

    # --- efecto del comprobante ---
    # Un valor explicito gana sobre solo_facturas: permite pedir los
    # complementos de pago sin tener que apagar tambien el default.
    if filtros.efecto_comprobante:
        q = q.filter(
            FacturasRecibidas.efecto_comprobante == filtros.efecto_comprobante
        )
    elif filtros.solo_facturas:
        q = q.filter(FacturasRecibidas.efecto_comprobante == EFECTO_INGRESO)

    # --- rango de fechas ---
    if filtros.fecha_desde:
        q = q.filter(FacturasRecibidas.fecha_emision >= filtros.fecha_desde)

    if filtros.fecha_hasta:
        # fecha_emision es DateTime y fecha_hasta es date. Comparar con <=
        # equivale a medianoche y perderia TODAS las facturas de ese dia
        # emitidas despues de las 00:00. Se usa el dia siguiente exclusivo.
        q = q.filter(
            FacturasRecibidas.fecha_emision < filtros.fecha_hasta + timedelta(days=1)
        )

    # --- rango de monto ---
    if filtros.monto_min is not None:
        q = q.filter(FacturasRecibidas.monto_total >= filtros.monto_min)

    if filtros.monto_max is not None:
        q = q.filter(FacturasRecibidas.monto_total <= filtros.monto_max)

    return q


def calcular_resumen_recibidas(
    db: Session,
    filtros: FiltrosFacturaRecibida,
) -> ResumenFacturasRecibidas:
    """
    Totales sobre el mismo conjunto que devuelve el listado.

    Se calcula en SQL, no iterando el resultado paginado: el resumen
    describe TODO el filtro, no la pagina que se esta viendo.
    """
    q = construir_query_facturas_recibidas(db, filtros)
    subconsulta = q.subquery()

    fila = db.query(
        func.count().label("total"),
        func.coalesce(func.sum(subconsulta.c.monto_total), 0).label("monto"),
        func.count(func.nullif(subconsulta.c.sat_estado != "Vigente", True))
            .label("vigentes"),
        func.count(func.nullif(subconsulta.c.sat_estado != "Cancelado", True))
            .label("canceladas"),
        func.count(func.distinct(subconsulta.c.rfc_emisor)).label("emisores"),
    ).select_from(subconsulta).one()

    return ResumenFacturasRecibidas(
        total_facturas=fila.total,
        total_monto=fila.monto,
        total_vigentes=fila.vigentes,
        total_canceladas=fila.canceladas,
        total_emisores=fila.emisores,
    )


def listar_emisores(db: Session) -> list[dict]:
    """
    Proveedores distintos con su conteo, para el dropdown del dashboard.

    No se filtra por nada: el dropdown debe ofrecer todos los emisores
    conocidos, no solo los del filtro activo, o el usuario no podria
    cambiar de proveedor una vez que filtro por uno.
    """
    filas = (
        db.query(
            FacturasRecibidas.rfc_emisor,
            func.max(FacturasRecibidas.nombre_emisor).label("nombre_emisor"),
            func.count().label("total_facturas"),
        )
        .group_by(FacturasRecibidas.rfc_emisor)
        .order_by(func.max(FacturasRecibidas.nombre_emisor))
        .all()
    )

    return [
        {
            "rfc_emisor": f.rfc_emisor,
            "nombre_emisor": f.nombre_emisor,
            "total_facturas": f.total_facturas,
        }
        for f in filas
    ]