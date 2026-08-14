from fastapi import APIRouter, Depends, HTTPException, Response, Query
from sqlalchemy.orm import Session
from app.BaseDeDatos import get_db
from app.modelos.orden_compra import OrdenesCompra
from app.modelos.factura import Facturas
from app.esquemas.orden_compra import OrdenCompraListado, OrdenCompraActualizar
from app.services.factura_service import reconciliar

router = APIRouter()

@router.get("/", response_model=list[OrdenCompraListado], tags=["Ordenes de compra"])
def listar_ordenes_compra(
    sin_numero: bool | None = Query(None, description="Solo OCs sin numero_oc capturado"),
    db : Session = Depends(get_db)
):
    consulta = db.query(OrdenesCompra)

    if sin_numero:
        consulta = consulta.filter(OrdenesCompra.numero_oc == None)

    ordenes = consulta.order_by(OrdenesCompra.fecha_recepcion.desc()).all()

    resultado = []
    for oc in ordenes:
        facturas_asociadas = db.query(Facturas).filter(
            Facturas.id_orden_compra == oc.id 
        ).count()

        resultado.append(OrdenCompraListado(
            id=oc.id,
            numero_oc=oc.numero_oc,
            numero_oc_detectado=oc.numero_oc_detectado,
            nombre_archivo=oc.nombre_archivo,
            fecha_recepcion=oc.fecha_recepcion,
            tiene_archivo=oc.archivo is not None,
            facturas_asociadas=facturas_asociadas
        ))

    return resultado

@router.get("/{id_oc}", response_model=OrdenCompraListado, tags=["Ordenes de compra"])
def obtener_orden_compra(
    id_oc: int,
    db: Session = Depends(get_db)
):
    oc = db.query(OrdenesCompra).filter(OrdenesCompra.id == id_oc).first()
    if not oc:
        raise HTTPException(status_code=404, detail="Orden de compra no encontrada")

    facturas_asociadas = db.query(Facturas).filter(
        Facturas.id_orden_compra == oc.id
    ).count()

    return OrdenCompraListado(
        id=oc.id,
        numero_oc=oc.numero_oc,
        numero_oc_detectado=oc.numero_oc_detectado,
        nombre_archivo=oc.nombre_archivo,
        fecha_recepcion=oc.fecha_recepcion,
        tiene_archivo=oc.archivo is not None,
        facturas_asociadas=facturas_asociadas
    )

@router.get("/{id_oc}/archivo", tags=["Ordenes de compra"])
def descargar_archivo_oc(id_oc: int, db: Session = Depends(get_db)):
    oc = db.query(OrdenesCompra).filter(OrdenesCompra.id == id_oc).first()
    if not oc or not oc.archivo:
        raise HTTPException(status_code=404, detail="Archivo no disponible")

    return Response(
        content=oc.archivo,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'inline; filename="{oc.nombre_archivo or oc.id}.pdf"'
        }
    )

@router.patch("/{id_oc}", response_model=OrdenCompraListado, tags=["Ordenes de compra"])
def actualizar_orden_compra(
    id_oc: int,
    datos: OrdenCompraActualizar,
    db: Session = Depends(get_db)
):
    oc = db.query(OrdenesCompra).filter(OrdenesCompra.id == id_oc).first()
    if not oc:
        raise HTTPException(status_code=404, detail="Orden de compra no encontrada")

    oc.numero_oc = datos.numero_oc
    db.commit()
    db.refresh(oc)

    # Reconciliar facturas asociadas a esta orden de compra
    reconciliar(db)

    facturas_asociadas = db.query(Facturas).filter(
        Facturas.id_orden_compra == oc.id
    ).count()

    return OrdenCompraListado(
        id=oc.id,
        numero_oc=oc.numero_oc,
        numero_oc_detectado=oc.numero_oc_detectado,
        nombre_archivo=oc.nombre_archivo,
        fecha_recepcion=oc.fecha_recepcion,
        tiene_archivo=oc.archivo is not None,
        facturas_asociadas=facturas_asociadas
    )