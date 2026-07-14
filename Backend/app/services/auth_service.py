from datetime import datetime, timezone
from app.modelos.usuario import Usuarios # Importar el modelo de usuario
from app.core.seguridad import verify_password, hash_password # Importar la función de verificación de contraseña y la función para crear el token de refresco
from sqlalchemy.orm import Session # Importar la clase Session de SQLAlchemy

def autenticar_usuario(correo: str, password: str, db: Session) -> Usuarios | None:
    """
    Función para autenticar un usuario.
    
    Args:
        correo (str): Correo electrónico del usuario.
        password (str): Contraseña proporcionada por el usuario.
        db (Session): Sesión de la base de datos.

    Returns:
        Usuarios | None: Retorna el objeto de usuario si la autenticación es exitosa,
    """
    # Buscar el usuario en la base de datos por correo electrónico
    usuario = db.query(Usuarios).filter(Usuarios.correo == correo).first()
    
    # Verificar si el usuario existe y si la contraseña es correcta
    if usuario and verify_password(password, usuario.password_hash):
        return usuario  # Retornar el objeto de usuario si la autenticación es exitosa
    
    return None  # Retornar None si la autenticación falla

def guardar_refresh_token(usuario: Usuarios, token: str, expiracion: datetime, db: Session) -> None:
    """
    Función para guardar el token de refresco y su fecha de expiración en la base de datos.
    
    Args:
        usuario (Usuarios): Objeto del usuario al que se le asignará el token de refresco.
        token (str): Token de refresco generado.
        expiracion (datetime): Fecha y hora de expiración del token.
        db (Session): Sesión de la base de datos.
    """
    # Asignar el token de refresco y su fecha de expiración al usuario
    usuario.refresh_token = token
    usuario.refresh_token_expiracion = expiracion
    
    db.commit()

def verificar_refresh_token(token: str, db: Session) -> Usuarios | None:
    """
    Función para verificar un token de refresco.
    
    Args:
        token (str): Token de refresco proporcionado por el usuario.
        db (Session): Sesión de la base de datos.

    Returns:
        Usuarios | None: Retorna el objeto de usuario si el token es válido y no ha expirado,
    """
    # Buscar el usuario en la base de datos por el token de refresco
    usuario = db.query(Usuarios).filter(Usuarios.refresh_token == token).first()
    
    # Verificar si el usuario existe y si el token no ha expirado
    if usuario and usuario.refresh_token_expiracion > datetime.now():
        return usuario  # Retornar el objeto de usuario si el token es válido
    
    return None  # Retornar None si el token es inválido o ha expirado

def registrar_usuario(nombre: str, correo: str, password: str, rol: str, db: Session) -> Usuarios:
    """
    Función para registrar un nuevo usuario en la base de datos.
    
    Args:
        nombre (str): Nombre del usuario.
        correo (str): Correo electrónico del usuario.
        password (str): Contraseña del usuario.
        rol (str): Rol del usuario.
        db (Session): Sesión de la base de datos.

    Returns:
        Usuarios: Retorna el objeto del usuario registrado con la contraseña hasheada.
    """
    usuario_existente = db.query(Usuarios).filter(Usuarios.correo == correo).first()

    if usuario_existente:
        raise ValueError("El correo ya está registrado.")
    
    # Hashear la contraseña antes de guardarla en la base de datos
    hash = hash_password(password)

    # Crear un nuevo objeto de usuario
    nuevo_usuario = Usuarios(
        nombre=nombre,
        correo=correo,
        password_hash=hash,
        rol=rol
    )
    
    # Agregar el nuevo usuario a la sesión y guardar los cambios en la base de datos
    db.add(nuevo_usuario)
    db.commit()
    db.refresh(nuevo_usuario)  # Refrescar el objeto para obtener el ID generado
    
    return nuevo_usuario  # Retornar el objeto del usuario registrado