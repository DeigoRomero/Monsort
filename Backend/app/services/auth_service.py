"""
Autenticación: login con bloqueo, refresh con rotación, registro con política
de contraseñas. Las rutas viven en app/api/rutas/auth.py.
"""
import logging
from datetime import datetime, timedelta

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.seguridad import (
    MAX_FALLOS_CUENTA, MINUTOS_BLOQUEO_CUENTA, comparar_con_señuelo,
    crear_refresh_token, hash_password, huella_token, validar_password,
    verify_password,
)
from app.modelos.usuario import Usuarios

logger = logging.getLogger("app.seguridad")


def _normalizar_correo(correo: str) -> str:
    return (correo or "").strip().lower()


def buscar_por_correo(db: Session, correo: str) -> Usuarios | None:
    return db.query(Usuarios).filter(
        func.lower(Usuarios.correo) == _normalizar_correo(correo)
    ).first()


def autenticar_usuario(correo: str, password: str, db: Session,
                       ip: str = "") -> Usuarios | None:
    """
    El usuario si la contraseña es correcta; None en cualquier otro caso.

    No distingue "no existe", "contraseña mal", "bloqueada" o "desactivada"
    hacia afuera: dar pistas le ahorra trabajo a quien prueba correos.
    La razón real queda en el log.
    """
    usuario = buscar_por_correo(db, correo)
    if not usuario:
        comparar_con_señuelo(password)   # mismo tiempo de respuesta
        logger.warning("Login fallido: correo no registrado (%s) desde %s", correo, ip)
        return None

    ahora = datetime.now()
    if usuario.bloqueado_hasta and usuario.bloqueado_hasta > ahora:
        comparar_con_señuelo(password)
        logger.warning("Login rechazado: cuenta %s bloqueada hasta %s (IP %s)",
                       usuario.correo, usuario.bloqueado_hasta, ip)
        return None

    if not usuario.activo:
        comparar_con_señuelo(password)
        logger.warning("Login rechazado: cuenta desactivada %s (IP %s)", usuario.correo, ip)
        return None

    if not verify_password(password, usuario.password_hash):
        usuario.intentos_fallidos = (usuario.intentos_fallidos or 0) + 1
        if usuario.intentos_fallidos >= MAX_FALLOS_CUENTA:
            usuario.bloqueado_hasta = ahora + timedelta(minutes=MINUTOS_BLOQUEO_CUENTA)
            usuario.intentos_fallidos = 0
            logger.warning("Cuenta %s bloqueada %d min tras %d intentos (IP %s)",
                           usuario.correo, MINUTOS_BLOQUEO_CUENTA, MAX_FALLOS_CUENTA, ip)
        else:
            logger.warning("Login fallido: contraseña incorrecta para %s (%d/%d) desde %s",
                           usuario.correo, usuario.intentos_fallidos, MAX_FALLOS_CUENTA, ip)
        db.commit()
        return None

    usuario.intentos_fallidos = 0
    usuario.bloqueado_hasta = None
    usuario.ultimo_acceso = ahora
    db.commit()
    logger.info("Login correcto: %s desde %s", usuario.correo, ip)
    return usuario


def emitir_refresh_token(usuario: Usuarios, db: Session) -> str:
    """Genera un refresh token nuevo, guarda su huella y devuelve el token."""
    token, expiracion = crear_refresh_token()
    usuario.refresh_token = huella_token(token)
    usuario.refresh_token_expiracion = expiracion
    db.commit()
    return token


def verificar_refresh_token(token: str | None, db: Session) -> Usuarios | None:
    if not token or len(token) > 200:
        return None
    usuario = db.query(Usuarios).filter(
        Usuarios.refresh_token == huella_token(token)
    ).first()
    if not usuario or not usuario.activo:
        return None
    if not usuario.refresh_token_expiracion or usuario.refresh_token_expiracion <= datetime.now():
        return None
    return usuario


def cerrar_sesion(usuario: Usuarios, db: Session) -> None:
    """Invalida el refresh token y TODOS los access token vivos del usuario."""
    usuario.refresh_token = None
    usuario.refresh_token_expiracion = None
    usuario.sesion_version = (usuario.sesion_version or 0) + 1
    db.commit()


def registrar_usuario(nombre: str, correo: str, password: str, rol: str, db: Session) -> Usuarios:
    correo = _normalizar_correo(correo)
    if buscar_por_correo(db, correo):
        raise ValueError("El correo ya está registrado.")

    problema = validar_password(password, correo)
    if problema:
        raise ValueError(problema)

    nuevo_usuario = Usuarios(
        nombre=nombre.strip(),
        correo=correo,
        password_hash=hash_password(password),
        rol=rol,
        activo=True,
        intentos_fallidos=0,
        sesion_version=0,
    )
    db.add(nuevo_usuario)
    db.commit()
    db.refresh(nuevo_usuario)
    return nuevo_usuario


def cambiar_password(usuario: Usuarios, actual: str, nueva: str, db: Session) -> None:
    if not verify_password(actual, usuario.password_hash):
        raise ValueError("La contraseña actual no es correcta.")
    problema = validar_password(nueva, usuario.correo)
    if problema:
        raise ValueError(problema)
    usuario.password_hash = hash_password(nueva)
    cerrar_sesion(usuario, db)   # cierra las demás sesiones abiertas
