from ..BaseDeDatos import Base
from sqlalchemy import Boolean, Column, DateTime, Integer, String, text
from sqlalchemy.orm import relationship
from app.modelos.factura import Facturas
    
# Heredar de la clase Base para crear modelos de la base de datos
class Usuarios(Base):
    __tablename__ = "Usuarios"
    id_usuario = Column(Integer, primary_key=True, index=True, autoincrement=True)
    nombre = Column(String, nullable=False)
    correo = Column(String, unique=True, nullable=False)
    password_hash = Column(String, nullable=False)
    rol = Column(String, nullable=False)
    # SHA-256 (hex) del refresh token vigente, NUNCA el token en claro.
    refresh_token = Column(String, nullable=True)
    refresh_token_expiracion = Column(DateTime, nullable=True)
    intentos_fallidos = Column(Integer, nullable=False, server_default=text("0"), default=0)
    bloqueado_hasta = Column(DateTime, nullable=True)
    # Va dentro del access token ("ver"). Subirla invalida todos los tokens
    # emitidos antes: logout, cambio de contraseña, cuenta comprometida.
    sesion_version = Column(Integer, nullable=False, server_default=text("0"), default=0)
    activo = Column(Boolean, nullable=False, server_default=text("true"), default=True)
    ultimo_acceso = Column(DateTime, nullable=True)
    facturas = relationship("Facturas", back_populates="usuario")
    historial_verificacion = relationship("HistorialVerificacion", back_populates="usuario") 

