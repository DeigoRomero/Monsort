from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from app.BaseDeDatos import SessionLocal
from app.services.factura_service import contar_facturas_pendientes
from app.modelos.factura import Facturas

router = APIRouter()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

@router.get("/pendientes/count", tags=["Facturas"])
def endpoint_contar_pendientes(db: Session = Depends(get_db)):
    return {"pendientes": contar_facturas_pendientes(db)}

@router.get("/", tags=["Facturas"])
def listar_facturas(db: Session = Depends(get_db)):
    return db.query(Facturas).all()
