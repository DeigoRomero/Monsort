from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.BaseDeDatos import get_db
from app.modelos.cliente import Cliente
from app.modelos.factura import Facturas
from app.modelos.orden_compra import OrdenesCompra
from app.esquemas.cliente import ClienteCrear, ClienteActualizar, ClienteListado, ClienteDetalle
from sqlalchemy.exc import IntegrityError

router = APIRouter()


@router.get("/", response_model=list[ClienteListado], tags=["Clientes"])
def listar_clientes(
    solo_activos: bool = True,
    db: Session = Depends(get_db)
):
    q = db.query(Cliente)
    if solo_activos:
        q = q.filter(Cliente.activo == True)
    return q.order_by(Cliente.nombre).all()


@router.get("/{id_cliente}", response_model=ClienteDetalle, tags=["Clientes"])
def obtener_cliente(id_cliente: int, db: Session = Depends(get_db)):
    cliente = db.query(Cliente).filter(Cliente.id == id_cliente).first()
    if not cliente:
        raise HTTPException(status_code=404, detail="Cliente no encontrado")

    total_facturas = db.query(Facturas).filter(
        Facturas.id_cliente == id_cliente
    ).count()

    total_ordenes = db.query(OrdenesCompra).filter(
        OrdenesCompra.id_cliente == id_cliente
    ).count()

    return ClienteDetalle(
        id=cliente.id,
        rfc=cliente.rfc,
        nombre=cliente.nombre,
        dias_plazo_pago=cliente.dias_plazo_pago,
        activo=cliente.activo,
        fecha_creacion=cliente.fecha_creacion,
        total_facturas=total_facturas,
        total_ordenes=total_ordenes,
    )


@router.post("/", response_model=ClienteListado, status_code=201, tags=["Clientes"])
def crear_cliente(datos: ClienteCrear, db: Session = Depends(get_db)):
    existente = db.query(Cliente).filter(Cliente.rfc == datos.rfc).first()
    if existente:
        raise HTTPException(status_code=409, detail="Ya existe un cliente con ese RFC")

    nuevo = Cliente(
        rfc=datos.rfc,
        nombre=datos.nombre,
        dias_plazo_pago=datos.dias_plazo_pago,
    )
    db.add(nuevo)
    try:
        db.commit()
        db.refresh(nuevo)
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="Ya existe un cliente con ese RFC")

    return nuevo


@router.patch("/{id_cliente}", response_model=ClienteListado, tags=["Clientes"])
def actualizar_cliente(
    id_cliente: int,
    datos: ClienteActualizar,
    db: Session = Depends(get_db)
):
    cliente = db.query(Cliente).filter(Cliente.id == id_cliente).first()
    if not cliente:
        raise HTTPException(status_code=404, detail="Cliente no encontrado")

    if datos.nombre is not None:
        cliente.nombre = datos.nombre
    if datos.dias_plazo_pago is not None:
        cliente.dias_plazo_pago = datos.dias_plazo_pago
    if datos.activo is not None:
        cliente.activo = datos.activo

    db.commit()
    db.refresh(cliente)
    return cliente