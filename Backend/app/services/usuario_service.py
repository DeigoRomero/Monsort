from app.modelos.usuario import Usuarios
from app.modelos.estados import Estados
from sqlalchemy.orm import Session
from app.BaseDeDatos import SessionLocal


def obtener_usuario_sistema(db: Session):
    return db.query(Usuarios).filter(Usuarios.nombre == "Sistema Automatico").first()
    
def obtener_estado_pendiente(db: Session):
    return db.query(Estados).filter(Estados.nombre_estado == "Pendiente de verificación").first()

def obtener_estado(db, nombre_estado):
    return db.query(Estados).filter(Estados.nombre_estado == nombre_estado).first()
