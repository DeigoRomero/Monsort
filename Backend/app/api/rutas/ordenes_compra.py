from datetime import date, datetime, time, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import exists, func, or_
from sqlalchemy.orm import Session, load_only

from app.BaseDeDatos import get_db
from app.core.descargas import respuesta_archivo
from app.modelos.cliente import Cliente
from app.modelos.factura import Facturas
from app.modelos.orden_compra import OrdenesCompra
from app.esquemas.orden_compra import (
    FiltrosOrdenCompra, OrdenCompraActualizar, OrdenCompraListado,
)
from app.services.factura_service import reconciliar
from app.services.query_builder import condiciones_monto

router = APIRouter()

# Columnas del listado. 'archivo' (el PDF) queda fuera: antes cada listado
# bajaba todos los PDFs de la base solo para saber si existían.
_COLUMNAS = (
    OrdenesCompra.id, OrdenesCompra.numero_oc, OrdenesCompra.numero_oc_detectado,
    OrdenesCompra.confianza_oc, OrdenesCompra.nombre_archivo,
    OrdenesCompra.fecha_recepcion, OrdenesCompra.id_cliente,
    OrdenesCompra.monto, OrdenesCompra.moneda,
)


def _tiene_factura():
    """EXISTS: la OC tiene al menos una factura vinculada."""
    return exists().where(Facturas.id_orden_compra == OrdenesCompra.id)


def _listado(db: Session, oc: OrdenesCompra, conteo: int | None = None,
             tiene_archivo: bool | None = None,
             cliente: str | None = None) -> OrdenCompraListado:
    if conteo is None:
        conteo = db.query(Facturas).filter(Facturas.id_orden_compra == oc.id).count()
    if tiene_archivo is None:
        tiene_archivo = db.query(OrdenesCompra.archivo.isnot(None)).filter(
            OrdenesCompra.id == oc.id
        ).scalar()
    # En el listado el nombre ya viene del JOIN; aquí solo para una OC suelta.
    if cliente is None and oc.id_cliente:
        cliente = db.query(Cliente.nombre).filter(Cliente.id == oc.id_cliente).scalar()

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
        id_cliente=oc.id_cliente,
        cliente=cliente,
        monto=oc.monto,
        moneda=oc.moneda,
    )


def _aplicar_filtros(consulta, f: FiltrosOrdenCompra):
    if f.sin_numero:
        consulta = consulta.filter(OrdenesCompra.numero_oc.is_(None))
    if f.id_cliente:
        consulta = consulta.filter(OrdenesCompra.id_cliente == f.id_cliente)
    if f.fecha_desde:
        consulta = consulta.filter(
            OrdenesCompra.fecha_recepcion >= datetime.combine(f.fecha_desde, time.min))
    if f.fecha_hasta:
        # Hasta = el día completo
        consulta = consulta.filter(
            OrdenesCompra.fecha_recepcion
            < datetime.combine(f.fecha_hasta + timedelta(days=1), time.min))
    if f.monto_min is not None:
        consulta = consulta.filter(OrdenesCompra.monto >= f.monto_min)
    if f.monto_max is not None:
        consulta = consulta.filter(OrdenesCompra.monto <= f.monto_max)
    if f.con_factura is True:
        consulta = consulta.filter(_tiene_factura())
    elif f.con_factura is False:
        consulta = consulta.filter(~_tiene_factura())

    q = (f.q or "").strip()
    if q:
        termino = f"%{q}%"
        condiciones = [
            OrdenesCompra.numero_oc.ilike(termino),
            OrdenesCompra.nombre_archivo.ilike(termino),
            Cliente.nombre.ilike(termino),
            # "1414" puede ser monto o parte de un número de OC: se buscan los dos.
            *condiciones_monto(q, OrdenesCompra.monto),
        ]
        consulta = consulta.filter(or_(*condiciones))
    return consulta


@router.get("/", response_model=list[OrdenCompraListado], tags=["Ordenes de compra"])
def listar_ordenes_compra(
    filtros: FiltrosOrdenCompra = Depends(),
    db: Session = Depends(get_db),
):
    if (filtros.fecha_desde and filtros.fecha_hasta
            and filtros.fecha_desde > filtros.fecha_hasta):
        raise HTTPException(status_code=422, detail="La fecha 'desde' es posterior a 'hasta'")
    if (filtros.monto_min is not None and filtros.monto_max is not None
            and filtros.monto_min > filtros.monto_max):
        raise HTTPException(status_code=422, detail="El monto mínimo es mayor que el máximo")

    consulta = (
        db.query(OrdenesCompra, Cliente.nombre)
        .options(load_only(*_COLUMNAS))
        .outerjoin(Cliente, Cliente.id == OrdenesCompra.id_cliente)
    )
    consulta = _aplicar_filtros(consulta, filtros)
    filas = consulta.order_by(OrdenesCompra.fecha_recepcion.desc().nullslast(),
                              OrdenesCompra.id.desc()).all()

    ids = [oc.id for oc, _ in filas]
    conteos = dict(
        db.query(Facturas.id_orden_compra, func.count())
        .filter(Facturas.id_orden_compra.in_(ids))
        .group_by(Facturas.id_orden_compra).all()
    ) if ids else {}
    con_archivo = {
        fila[0] for fila in db.query(OrdenesCompra.id)
        .filter(OrdenesCompra.id.in_(ids), OrdenesCompra.archivo.isnot(None)).all()
    } if ids else set()

    return [
        _listado(db, oc, conteos.get(oc.id, 0), oc.id in con_archivo, nombre)
        for oc, nombre in filas
    ]


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

    cambios = datos.model_dump(exclude_unset=True)
    if cambios.get("id_cliente") is not None and not db.query(Cliente.id).filter(
        Cliente.id == cambios["id_cliente"]
    ).first():
        raise HTTPException(status_code=422, detail="Cliente no encontrado")

    renumerada = "numero_oc" in cambios and cambios["numero_oc"] != oc.numero_oc
    for campo, valor in cambios.items():
        setattr(oc, campo, valor)
    if "monto" in cambios and cambios["monto"] is not None and not oc.moneda:
        oc.moneda = "MXN"
    if "numero_oc" in cambios:
        # Lo capturó una persona: ya no es una detección que haya que revisar.
        oc.confianza_oc = "manual"
    db.commit()
    db.refresh(oc)

    if renumerada:
        reconciliar(db)
    return _listado(db, oc)
