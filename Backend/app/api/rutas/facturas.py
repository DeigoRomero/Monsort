from fastapi import APIRouter, Depends, HTTPException, Response, Query
from sqlalchemy.orm import Session
from math import ceil

from app.esquemas.orden_compra import OrdenCompraResumen, OrdenCompraActualizar, OrdenCompraListado
from app.BaseDeDatos import get_db
from app.modelos.factura import Facturas
from app.modelos.estados import Estados
from app.modelos.orden_compra import OrdenesCompra
from app.modelos.complemento_pago import ComplementosPago
from app.modelos.cp_documento_relacionado import CPDocumentosRelacionados
from app.esquemas.factura import (
    FacturaListado, FacturaDetalle, FacturaActualizar,
    ConceptoDetalle, ComplementoResumen, VincularOCRequest,
    FiltrosFactura, ResumenFacturas, CancelarRequest, FacturaListadoConResumen,
)
from app.services.factura_service import (
    contar_facturas_pendientes, reconciliar,
    cancelar_factura, cancelar_cp,
)
from app.services.query_builder import construir_query_facturas, calcular_resumen

router = APIRouter()


# ─────────────────────────────────────────────
# HELPERS INTERNOS
# ─────────────────────────────────────────────

def _obtener_usuario_actual(db: Session) -> int:
    """
    Placeholder hasta que integres JWT.
    Devuelve el id_usuario del usuario sistema.
    Reemplaza con: token_data.id_usuario cuando tengas auth.
    """
    from app.services.usuario_service import obtener_usuario_sistema
    return obtener_usuario_sistema(db).id_usuario


def _tiene_cp(db: Session, id_factura: int) -> bool:
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


def _factura_a_listado(f: Facturas, tiene_cp: bool) -> FacturaListado:
    return FacturaListado(
        id_factura=f.id_factura,
        folio_fiscal=f.folio_fiscal,
        folio_interno=f.folio_interno,
        cliente=f.cliente,
        rfc=f.rfc,
        fecha=f.fecha,
        numero_oc=f.numero_oc,
        total=f.total,
        moneda=f.moneda,
        tipo_cambio=f.tipo_cambio,
        fecha_liquidacion=f.fecha_liquidacion,
        fecha_validacion=f.fecha_validacion,
        estado=f.estado.nombre_estado,
        tiene_pdf=f.pdf_factura is not None,
        tiene_xml=f.xml_factura is not None,
        tiene_oc=f.orden_compra_archivo is not None or f.id_orden_compra is not None,
        tiene_cp=tiene_cp,
    )


# ─────────────────────────────────────────────
# LISTADO PRINCIPAL  (búsqueda + filtros + resumen)
# ─────────────────────────────────────────────

@router.get("/", tags=["Facturas"])
def listar_facturas(
    filtros: FiltrosFactura = Depends(),
    db: Session = Depends(get_db),
) -> FacturaListadoConResumen:
    """
    Listado paginado con búsqueda, filtros y resumen de totales.

    Parámetros de query (todos opcionales):
      q               — búsqueda libre: cliente, UUID, folio_interno, numero_oc
      cliente         — filtro exacto por nombre de cliente (ilike)
      numero_oc       — filtro por número de OC (ilike)
      con_cp          — true/false: tiene o no CP vinculado
      incluir_canceladas — false por defecto
      fecha_desde     — YYYY-MM-DD
      fecha_hasta     — YYYY-MM-DD
      pagina          — default 1
      por_pagina      — default 50
    """
    q = construir_query_facturas(db, filtros)
    total_registros = q.count()
    total_paginas = ceil(total_registros / filtros.por_pagina) if total_registros else 1

    offset = (filtros.pagina - 1) * filtros.por_pagina
    facturas = q.order_by(Facturas.fecha.desc()).offset(offset).limit(filtros.por_pagina).all()

    resumen = calcular_resumen(db, filtros)

    resultado = [
        _factura_a_listado(f, _tiene_cp(db, f.id_factura))
        for f in facturas
    ]

    return FacturaListadoConResumen(
        facturas=resultado,
        resumen=resumen,
        pagina=filtros.pagina,
        por_pagina=filtros.por_pagina,
        total_paginas=total_paginas,
    )


# ─────────────────────────────────────────────
# RESUMEN STANDALONE  (para el dashboard sin listado)
# ─────────────────────────────────────────────

@router.get("/resumen", response_model=ResumenFacturas, tags=["Facturas"])
def obtener_resumen(
    filtros: FiltrosFactura = Depends(),
    db: Session = Depends(get_db),
):
    """Totales calculados en SQL sobre el mismo filtro del listado."""
    return calcular_resumen(db, filtros)


# ─────────────────────────────────────────────
# ESTADOS  (dropdown del dashboard)
# ─────────────────────────────────────────────

@router.get("/estados", tags=["Facturas"])
def listar_estados(db: Session = Depends(get_db)):
    estados = db.query(Estados).all()
    return [
        {
            "id_estado": e.id_estado,
            "nombre_estado": e.nombre_estado,
            "descripcion_estado": e.descripcion_estado,
        }
        for e in estados
    ]


# ─────────────────────────────────────────────
# PENDIENTES  (badge del dashboard)
# ─────────────────────────────────────────────

@router.get("/pendientes/count", tags=["Facturas"])
def endpoint_contar_pendientes(db: Session = Depends(get_db)):
    return {"pendientes": contar_facturas_pendientes(db)}


# ─────────────────────────────────────────────
# DETALLE
# ─────────────────────────────────────────────

@router.get("/{id_factura}", response_model=FacturaDetalle, tags=["Facturas"])
def obtener_factura(id_factura: int, db: Session = Depends(get_db)):
    f = db.query(Facturas).filter(Facturas.id_factura == id_factura).first()
    if not f:
        raise HTTPException(status_code=404, detail="Factura no encontrada")

    conceptos = [
        ConceptoDetalle(
            descripcion=c.descripcion,
            cantidad=c.cantidad,
            unidad=c.unidad,
            precio_unitario=c.precio_unitario,
            importe=c.importe,
        )
        for c in f.conceptos
    ]

    docs = (
        db.query(CPDocumentosRelacionados)
        .join(ComplementosPago,
              CPDocumentosRelacionados.id_complemento == ComplementosPago.id)
        .filter(
            CPDocumentosRelacionados.id_factura == id_factura,
            ComplementosPago.cancelado == False,   # noqa: E712
        )
        .all()
    )

    complementos = []
    for d in docs:
        cp = db.query(ComplementosPago).filter(ComplementosPago.id == d.id_complemento).first()
        if cp:
            complementos.append(ComplementoResumen(
                id=cp.id,
                folio=cp.folio,
                fecha_pago=cp.fecha_pago,
                monto=cp.monto,
                imp_pagado=d.imp_pagado,
                imp_saldo_insoluto=d.imp_saldo_insoluto,
                num_parcialidad=d.num_parcialidad,
            ))

    orden_compra = None
    if f.orden_compra:
        orden_compra = OrdenCompraResumen(
            id=f.orden_compra.id,
            numero_oc=f.orden_compra.numero_oc,
            numero_oc_detectado=f.orden_compra.numero_oc_detectado,
            nombre_archivo=f.orden_compra.nombre_archivo,
            fecha_recepcion=f.orden_compra.fecha_recepcion,
            tiene_archivo=f.orden_compra.archivo is not None,
        )

    return FacturaDetalle(
        id_factura=f.id_factura,
        folio_fiscal=f.folio_fiscal,
        folio_interno=f.folio_interno,
        cliente=f.cliente,
        rfc=f.rfc,
        fecha=f.fecha,
        numero_oc=f.numero_oc,
        numero_oc_detectado=f.numero_oc_detectado,
        subtotal=f.subtotal,
        iva=f.iva,
        total=f.total,
        moneda=f.moneda,
        tipo_cambio=f.tipo_cambio,
        fecha_liquidacion=f.fecha_liquidacion,
        fecha_validacion=f.fecha_validacion,
        estado=f.estado.nombre_estado,
        orden_compra=orden_compra,
        tiene_pdf=f.pdf_factura is not None,
        tiene_xml=f.xml_factura is not None,
        conceptos=conceptos,
        complementos=complementos,
    )


# ─────────────────────────────────────────────
# CORRECCIÓN MANUAL
# ─────────────────────────────────────────────

@router.patch("/{id_factura}", response_model=FacturaDetalle, tags=["Facturas"])
def actualizar_factura(
    id_factura: int,
    datos: FacturaActualizar,
    db: Session = Depends(get_db),
):
    f = db.query(Facturas).filter(Facturas.id_factura == id_factura).first()
    if not f:
        raise HTTPException(status_code=404, detail="Factura no encontrada")

    if datos.numero_oc is not None:
        f.numero_oc = datos.numero_oc
        f.id_orden_compra = None

    if datos.folio_interno is not None:
        f.folio_interno = datos.folio_interno

    if datos.fecha_validacion is not None:
        f.fecha_validacion = datos.fecha_validacion

    db.commit()
    reconciliar(db)
    return obtener_factura(id_factura, db)


# ─────────────────────────────────────────────
# CANCELACIÓN DE FACTURA
# ─────────────────────────────────────────────

@router.patch("/{id_factura}/cancelar", response_model=FacturaDetalle, tags=["Facturas"])
def cancelar_factura_endpoint(
    id_factura: int,
    datos: CancelarRequest,
    db: Session = Depends(get_db),
):
    """
    Cambia el estado de la factura a 'Cancelada'.
    No borra nada de la BD. Registra el evento en HistorialVerificacion.
    """
    id_usuario = _obtener_usuario_actual(db)

    try:
        cancelar_factura(db, id_factura, datos.motivo, id_usuario)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    return obtener_factura(id_factura, db)


# ─────────────────────────────────────────────
# CANCELACIÓN DE CP
# ─────────────────────────────────────────────

@router.patch("/cp/{id_cp}/cancelar", tags=["Facturas"])
def cancelar_cp_endpoint(
    id_cp: int,
    datos: CancelarRequest,
    db: Session = Depends(get_db),
):
    """
    Cancela un CP administrativamente.
    Revierte fecha_liquidacion en las facturas que liquidó
    y llama a reconciliar() para recalcular sus estados.
    """
    id_usuario = _obtener_usuario_actual(db)

    try:
        cp = cancelar_cp(db, id_cp, datos.motivo, id_usuario)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    return {
        "id": cp.id,
        "uuid_cp": cp.uuid_cp,
        "cancelado": cp.cancelado,
        "fecha_cancelacion": cp.fecha_cancelacion,
        "motivo_cancelacion": cp.motivo_cancelacion,
    }


# ─────────────────────────────────────────────
# VINCULAR OC MANUAL
# ─────────────────────────────────────────────

@router.post("/{id_factura}/vincular-oc", response_model=FacturaDetalle, tags=["Facturas"])
def vincular_oc_manual(
    id_factura: int,
    datos: VincularOCRequest,
    db: Session = Depends(get_db),
):
    f = db.query(Facturas).filter(Facturas.id_factura == id_factura).first()
    if not f:
        raise HTTPException(status_code=404, detail="Factura no encontrada")

    oc = db.query(OrdenesCompra).filter(OrdenesCompra.id == datos.id_orden_compra).first()
    if not oc:
        raise HTTPException(status_code=404, detail="Orden de compra no encontrada")

    otras_facturas = db.query(Facturas).filter(
        Facturas.id_orden_compra == oc.id,
        Facturas.id_factura != id_factura,
    ).all()

    if otras_facturas and not datos.forzar:
        raise HTTPException(
            status_code=409,
            detail={
                "mensaje": "Esta orden de compra ya está vinculada a otra(s) factura(s).",
                "facturas_en_conflicto": [
                    {
                        "id_factura": otra.id_factura,
                        "folio_fiscal": otra.folio_fiscal,
                        "folio_interno": otra.folio_interno,
                    }
                    for otra in otras_facturas
                ],
            },
        )

    if otras_facturas and datos.forzar:
        for otra in otras_facturas:
            otra.id_orden_compra = None

    f.id_orden_compra = oc.id
    db.commit()
    reconciliar(db)
    return obtener_factura(id_factura, db)


# ─────────────────────────────────────────────
# OCS CANDIDATAS
# ─────────────────────────────────────────────

@router.get("/{id_factura}/ocs-candidatas", response_model=list[OrdenCompraListado], tags=["Facturas"])
def obtener_ocs_candidatas(id_factura: int, db: Session = Depends(get_db)):
    f = db.query(Facturas).filter(Facturas.id_factura == id_factura).first()
    if not f:
        raise HTTPException(status_code=404, detail="Factura no encontrada")

    if not f.numero_oc:
        return []

    ocs = db.query(OrdenesCompra).filter(OrdenesCompra.numero_oc == f.numero_oc).all()

    return [
        OrdenCompraListado(
            id=oc.id,
            numero_oc=oc.numero_oc,
            numero_oc_detectado=oc.numero_oc_detectado,
            nombre_archivo=oc.nombre_archivo,
            fecha_recepcion=oc.fecha_recepcion,
            tiene_archivo=oc.archivo is not None,
            facturas_asociadas=db.query(Facturas)
                .filter(Facturas.id_orden_compra == oc.id)
                .count(),
        )
        for oc in ocs
    ]


# ─────────────────────────────────────────────
# ARCHIVOS
# ─────────────────────────────────────────────

@router.get("/{id_factura}/pdf", tags=["Facturas"])
def descargar_pdf(id_factura: int, db: Session = Depends(get_db)):
    f = db.query(Facturas).filter(Facturas.id_factura == id_factura).first()
    if not f or not f.pdf_factura:
        raise HTTPException(status_code=404, detail="PDF no disponible")

    return Response(
        content=f.pdf_factura,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'inline; filename="{f.folio_interno or f.id_factura}.pdf"'
        },
    )


@router.get("/{id_factura}/xml", tags=["Facturas"])
def descargar_xml(id_factura: int, db: Session = Depends(get_db)):
    f = db.query(Facturas).filter(Facturas.id_factura == id_factura).first()
    if not f or not f.xml_factura:
        raise HTTPException(status_code=404, detail="XML no disponible")

    return Response(
        content=f.xml_factura,
        media_type="application/xml",
        headers={
            "Content-Disposition": f'attachment; filename="{f.folio_interno or f.id_factura}.xml"'
        },
    )