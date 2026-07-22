from app.BaseDeDatos import Base
from sqlalchemy import Column, Integer, String

class Configuracion_sistema(Base):
    __tablename__ = "configuracion_sistema"
    id_CS = Column(Integer, primary_key=True, index=True, autoincrement=True)
    clave = Column(String, nullable=False, unique=True)
    valor = Column(String, nullable=False)