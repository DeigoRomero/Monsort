from fastapi import APIRouter, Depends, HTTPException, Response, Query
from sqlalchemy.orm import Session
from app.BaseDeDatos import get_db
from app.modelos.factura import Facturas, Conceptos
from app.modelos.estados import Estados
from app.modelos.complemento_pago import ComplementosPago
from app.modelos.cp_documento_relacionado import CPDocumentosRelacionados
from app.esquemas.factura import (
    FacturaListado, FacturaDetalle, FacturaActualizar,
    ConceptoDetalle, ComplementoResumen
)
from app.services.factura_service import contar_facturas_pendientes, reconciliar

router = APIRouter()


# ---------- LISTADO ----------

@router.get("/", response_model=list[FacturaListado], tags=["Facturas"])
def listar_facturas(
    estado: str | None = Query(None, description="Filtrar por nombre de estado"),
    db: Session = Depends(get_db)
):
    consulta = db.query(Facturas)

    if estado:
        consulta = consulta.join(Estados).filter(Estados.nombre_estado == estado)

    facturas = consulta.order_by(Facturas.fecha.desc()).all()

    resultado = []
    for f in facturas:
        tiene_cp = db.query(CPDocumentosRelacionados).filter(
            CPDocumentosRelacionados.id_factura == f.id_factura
        ).first() is not None

        resultado.append(FacturaListado(
            id_factura=f.id_factura,
            folio_fiscal=f.folio_fiscal,
            folio_interno=f.folio_interno,
            cliente=f.cliente,
            rfc=f.rfc,
            fecha=f.fecha,
            numero_oc=f.numero_oc,
            total=f.total,
            tipo_cambio=f.tipo_cambio,
            fecha_liquidacion=f.fecha_liquidacion,
            estado=f.estado.nombre_estado,
            tiene_pdf=f.pdf_factura is not None,
            tiene_xml=f.xml_factura is not None,
            tiene_oc=f.orden_compra_archivo is not None or f.id_orden_compra is not None,
            tiene_cp=tiene_cp
        ))

    return resultado


@router.get("/estados", tags=["Facturas"])
def listar_estados(db: Session = Depends(get_db)):
    """Para poblar el dropdown de filtros del dashboard."""
    estados = db.query(Estados).all()
    return [
        {
            "id_estado": e.id_estado,
            "nombre_estado": e.nombre_estado,
            "descripcion_estado": e.descripcion_estado
        }
        for e in estados
    ]


@router.get("/pendientes/count", tags=["Facturas"])
def endpoint_contar_pendientes(db: Session = Depends(get_db)):
    return {"pendientes": contar_facturas_pendientes(db)}


# ---------- DETALLE ----------

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
            importe=c.importe
        )
        for c in f.conceptos
    ]

    # CPs que tocan esta factura
    docs = db.query(CPDocumentosRelacionados).filter(
        CPDocumentosRelacionados.id_factura == id_factura
    ).all()

    complementos = []
    for d in docs:
        cp = db.query(ComplementosPago).filter(
            ComplementosPago.id == d.id_complemento
        ).first()
        if cp:
            complementos.append(ComplementoResumen(
                id=cp.id,
                folio=cp.folio,
                fecha_pago=cp.fecha_pago,
                monto=cp.monto,
                imp_pagado=d.imp_pagado,
                imp_saldo_insoluto=d.imp_saldo_insoluto,
                num_parcialidad=d.num_parcialidad
            ))

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
        tipo_cambio=f.tipo_cambio,
        fecha_liquidacion=f.fecha_liquidacion,
        estado=f.estado.nombre_estado,
        id_orden_compra=f.id_orden_compra,
        tiene_pdf=f.pdf_factura is not None,
        tiene_xml=f.xml_factura is not None,
        conceptos=conceptos,
        complementos=complementos
    )


# ---------- CORRECCION MANUAL ----------

@router.patch("/{id_factura}", response_model=FacturaDetalle, tags=["Facturas"])
def actualizar_factura(
    id_factura: int,
    datos: FacturaActualizar,
    db: Session = Depends(get_db)
):
    f = db.query(Facturas).filter(Facturas.id_factura == id_factura).first()
    if not f:
        raise HTTPException(status_code=404, detail="Factura no encontrada")

    if datos.numero_oc is not None:
        f.numero_oc = datos.numero_oc
        # Si cambio el numero, el enlace anterior ya no aplica
        f.id_orden_compra = None

    if datos.folio_interno is not None:
        f.folio_interno = datos.folio_interno

    db.commit()

    # Reintentar el enlace con el nuevo numero
    reconciliar(db)

    return obtener_factura(id_factura, db)


# ---------- ARCHIVOS ----------

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
        }
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
        }
    )