"""
Rutas de sesión.

    POST /auth/login            público (con límite por IP y bloqueo por cuenta)
    POST /auth/refresh          cookie HttpOnly + encabezado anti-CSRF
    POST /auth/logout           cookie o Bearer
    GET  /auth/yo               Bearer
    POST /auth/cambiar-password Bearer
    POST /auth/registro         Bearer + rol administrador/desarrollador
"""
import logging
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy.orm import Session

from app.BaseDeDatos import get_db
from app.core.config import settings
from app.core.dependencias import (
    ROLES_ADMIN, ip_cliente, requiere_roles, usuario_actual,
)
from app.core.seguridad import (
    NOMBRE_COOKIE_REFRESH, REFRESH_DIAS, _minutos_access, crear_token_acceso,
    limitador_login_ip, limitador_refresh_ip,
)
from app.esquemas.usuario import (
    CambiarPasswordRequest, LoginRequest, LoginResponse, RegistroRequest,
    UsuarioResponse,
)
from app.modelos.usuario import Usuarios
from app.services.auth_service import (
    autenticar_usuario, cambiar_password, cerrar_sesion, emitir_refresh_token,
    registrar_usuario, verificar_refresh_token,
)

router = APIRouter()
logger = logging.getLogger("app.seguridad")

CREDENCIALES_INVALIDAS = (
    "Correo o contraseña incorrectos. Después de varios intentos fallidos la "
    "cuenta se bloquea 15 minutos."
)


# ─────────────────────────────────────────────
# Cookie del refresh token
# ─────────────────────────────────────────────

def _ruta_cookie(request: Request) -> str:
    # En producción nginx quita "/api" y uvicorn corre con --root-path /api:
    # el navegador ve /api/auth/..., así que la cookie debe decir eso.
    return f"{request.scope.get('root_path', '').rstrip('/')}/auth"


def _poner_cookie(response: Response, request: Request, token: str) -> None:
    response.set_cookie(
        NOMBRE_COOKIE_REFRESH, token,
        max_age=REFRESH_DIAS * 24 * 3600,
        httponly=True,
        secure=settings.ENTORNO != "desarrollo",
        samesite="strict",
        path=_ruta_cookie(request),
    )


def _borrar_cookie(response: Response, request: Request) -> None:
    response.delete_cookie(
        NOMBRE_COOKIE_REFRESH, path=_ruta_cookie(request),
        secure=settings.ENTORNO != "desarrollo", httponly=True, samesite="strict",
    )


def _origenes_permitidos(request: Request) -> set[str]:
    propios = {o.strip().rstrip("/") for o in settings.CORS_ORIGINS.split(",") if o.strip()}
    host = request.headers.get("host")
    if host:
        propios.add(f"{request.url.scheme}://{host}")
    return propios


def _exigir_mismo_origen(request: Request) -> None:
    """
    Defensa CSRF para las rutas que se autentican con la cookie.

    1. Encabezado X-Requested-With: un formulario de otro sitio no puede
       ponerlo, y un fetch de otro sitio que lo ponga dispara un preflight
       CORS que el servidor rechaza.
    2. Si viene Origin, debe ser uno de los nuestros.
    SameSite=Strict en la cookie ya bloquea casi todo; esto es la segunda capa.
    """
    if not request.headers.get("x-requested-with"):
        raise HTTPException(status_code=403, detail="Solicitud rechazada")
    origen = request.headers.get("origin")
    if origen:
        permitido = origen.rstrip("/") in _origenes_permitidos(request)
        if not permitido and settings.ENTORNO == "desarrollo":
            permitido = urlparse(origen).hostname in ("localhost", "127.0.0.1")
        if not permitido:
            logger.warning("Refresh/logout con Origin ajeno: %s", origen)
            raise HTTPException(status_code=403, detail="Solicitud rechazada")


def _respuesta_sesion(usuario: Usuarios) -> LoginResponse:
    return LoginResponse(
        access_token=crear_token_acceso(
            data={"sub": usuario.correo, "rol": usuario.rol},
            version=usuario.sesion_version or 0,
        ),
        token_type="bearer",
        expira_en=_minutos_access() * 60,
        usuario=UsuarioResponse.model_validate(usuario),
    )


# ─────────────────────────────────────────────
# Rutas
# ─────────────────────────────────────────────

@router.post("/login", response_model=LoginResponse)
def login(credenciales: LoginRequest, request: Request, response: Response,
          db: Session = Depends(get_db)) -> LoginResponse:
    ip = ip_cliente(request)
    espera = limitador_login_ip.bloqueado(ip)
    if espera:
        logger.warning("Login bloqueado por límite de IP: %s", ip)
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Demasiados intentos desde esta conexión. Intenta más tarde.",
            headers={"Retry-After": str(espera)},
        )

    usuario = autenticar_usuario(credenciales.correo, credenciales.password, db, ip)
    if not usuario:
        limitador_login_ip.registrar(ip)
        raise HTTPException(status_code=401, detail=CREDENCIALES_INVALIDAS)

    _poner_cookie(response, request, emitir_refresh_token(usuario, db))
    return _respuesta_sesion(usuario)


@router.post("/refresh", response_model=LoginResponse)
def refresh_token(request: Request, response: Response,
                  db: Session = Depends(get_db)) -> LoginResponse:
    _exigir_mismo_origen(request)
    ip = ip_cliente(request)
    if limitador_refresh_ip.bloqueado(ip):
        raise HTTPException(status_code=429, detail="Demasiadas solicitudes")
    limitador_refresh_ip.registrar(ip)

    usuario = verificar_refresh_token(request.cookies.get(NOMBRE_COOKIE_REFRESH), db)
    if not usuario:
        _borrar_cookie(response, request)
        raise HTTPException(
            status_code=401, detail="Sesión expirada. Vuelve a iniciar sesión.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Rotación: el token que se acaba de usar deja de servir.
    _poner_cookie(response, request, emitir_refresh_token(usuario, db))
    return _respuesta_sesion(usuario)


@router.post("/logout", status_code=204)
def logout(request: Request, response: Response, db: Session = Depends(get_db)):
    _exigir_mismo_origen(request)
    usuario = verificar_refresh_token(request.cookies.get(NOMBRE_COOKIE_REFRESH), db)
    if usuario:
        cerrar_sesion(usuario, db)
        logger.info("Logout: %s", usuario.correo)
    _borrar_cookie(response, request)
    response.status_code = 204
    return response


@router.get("/yo", response_model=UsuarioResponse)
def yo(usuario: Usuarios = Depends(usuario_actual)):
    return UsuarioResponse.model_validate(usuario)


@router.post("/cambiar-password", status_code=204)
def cambiar_mi_password(datos: CambiarPasswordRequest, request: Request, response: Response,
                        usuario: Usuarios = Depends(usuario_actual),
                        db: Session = Depends(get_db)):
    try:
        cambiar_password(usuario, datos.actual, datos.nueva, db)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    logger.info("Cambio de contraseña: %s", usuario.correo)
    _borrar_cookie(response, request)
    response.status_code = 204
    return response


@router.post("/registro", response_model=UsuarioResponse)
def registrar_usuarios(
    datos: RegistroRequest,
    admin: Usuarios = Depends(requiere_roles(*ROLES_ADMIN)),
    db: Session = Depends(get_db),
) -> UsuarioResponse:
    """
    Solo un administrador o desarrollador crea cuentas. Hasta el 30/09/2026
    esta ruta era pública y aceptaba cualquier rol: cualquiera con la URL
    podía darse de alta como administrador.
    """
    if datos.rol == "desarrollador" and (admin.rol or "").lower() != "desarrollador":
        raise HTTPException(status_code=403,
                            detail="Solo un desarrollador puede crear otro desarrollador")
    try:
        nuevo = registrar_usuario(datos.nombre, datos.correo, datos.password, datos.rol, db)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    logger.info("Usuario creado: %s (%s) por %s", nuevo.correo, nuevo.rol, admin.correo)
    return UsuarioResponse.model_validate(nuevo)
