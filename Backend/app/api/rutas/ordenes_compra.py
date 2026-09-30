from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func
from sqlalchemy.orm import Session, load_only

from app.BaseDeDatos import get_db
from app.core.descargas import respuesta_archivo
from app.modelos.factura import Facturas
from app.modelos.orden_compra import OrdenesCompra
from app.esquemas.orden_compra import OrdenCompraListado, OrdenCompraActualizar
from app.services.factura_service import reconciliar

router = APIRouter()

# Columnas del listado. 'archivo' (el PDF) queda fuera: antes cada listado
# bajaba todos los PDFs de la base solo para saber si existían.
_COLUMNAS = (
    OrdenesCompra.id, OrdenesCompra.numero_oc, OrdenesCompra.numero_oc_detectado,
    OrdenesCompra.confianza_oc, OrdenesCompra.nombre_archivo,
    OrdenesCompra.fecha_recepcion,
)


def _listado(db: Session, oc: OrdenesCompra, conteo: int | None = None,
             tiene_archivo: bool | None = None) -> OrdenCompraListado:
    if conteo is None:
        conteo = db.query(Facturas).filter(Facturas.id_orden_compra == oc.id).count()
    if tiene_archivo is None:
        tiene_archivo = db.query(OrdenesCompra.archivo.isnot(None)).filter(
            OrdenesCompra.id == oc.id
        ).scalar()

    sin_factura_30_dias = False
    if conteo == 0 and oc.fecha_recepcion:
        sin_factura_30_dias = (date.today() - oc.fecha_recepcion.date()).days > 30

    return OrdenCompraListado(
        id=oc.id,
        numero_oc=oc.numero_oc,
        numero_oc_detectado=oc.numero_oc_detectado,
        confianza_oc=oc.confianza_oc,
        nombre_archivo=oc.nombre_archivo,
        fecha_recepcion=oc.fecha_recepcion,
        tiene_archivo=bool(tiene_archivo),
        facturas_asociadas=conteo,
        sin_factura_30_dias=sin_factura_30_dias,
    )


@router.get("/", response_model=list[OrdenCompraListado], tags=["Ordenes de compra"])
def listar_ordenes_compra(
    sin_numero: bool | None = Query(None, description="Solo OCs sin numero_oc capturado"),
    db: Session = Depends(get_db),
):
    consulta = db.query(OrdenesCompra).options(load_only(*_COLUMNAS))
    if sin_numero:
        consulta = consulta.filter(OrdenesCompra.numero_oc.is_(None))
    ordenes = consulta.order_by(OrdenesCompra.fecha_recepcion.desc()).all()

    ids = [oc.id for oc in ordenes]
    conteos = dict(
        db.query(Facturas.id_orden_compra, func.count())
        .filter(Facturas.id_orden_compra.in_(ids))
        .group_by(Facturas.id_orden_compra).all()
    ) if ids else {}
    con_archivo = {
        fila[0] for fila in db.query(OrdenesCompra.id)
        .filter(OrdenesCompra.id.in_(ids), OrdenesCompra.archivo.isnot(None)).all()
    } if ids else set()

    return [_listado(db, oc, conteos.get(oc.id, 0), oc.id in con_archivo) for oc in ordenes]


@router.get("/{id_oc}", response_model=OrdenCompraListado, tags=["Ordenes de compra"])
def obtener_orden_compra(id_oc: int, db: Session = Depends(get_db)):
    oc = db.query(OrdenesCompra).options(load_only(*_COLUMNAS)).filter(
        OrdenesCompra.id == id_oc
    ).first()
    if not oc:
        raise HTTPException(status_code=404, detail="Orden de compra no encontrada")
    return _listado(db, oc)


@router.get("/{id_oc}/archivo", tags=["Ordenes de compra"])
def descargar_archivo_oc(id_oc: int, db: Session = Depends(get_db)):
    oc = db.query(OrdenesCompra).filter(OrdenesCompra.id == id_oc).first()
    if not oc or not oc.archivo:
        raise HTTPException(status_code=404, detail="Archivo no disponible")
    return respuesta_archivo(oc.archivo, oc.nombre_archivo, respaldo=f"OC_{oc.id}")


@router.patch("/{id_oc}", response_model=OrdenCompraListado, tags=["Ordenes de compra"])
def actualizar_orden_compra(
    id_oc: int,
    datos: OrdenCompraActualizar,
    db: Session = Depends(get_db),
):
    oc = db.query(OrdenesCompra).options(load_only(*_COLUMNAS)).filter(
        OrdenesCompra.id == id_oc
    ).first()
    if not oc:
        raise HTTPException(status_code=404, detail="Orden de compra no encontrada")

    oc.numero_oc = datos.numero_oc
    # Lo capturó una persona: ya no es una detección que haya que revisar.
    oc.confianza_oc = "manual"
    db.commit()
    db.refresh(oc)

    reconciliar(db)
    return _listado(db, oc)
