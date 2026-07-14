from fastapi import APIRouter, HTTPException, Depends
from app.esquemas import usuario
from app.esquemas.usuario import RefreshTokenRequest, UsuarioResponse
from app.services.auth_service import autenticar_usuario, verificar_refresh_token
from app.esquemas.usuario import LoginRequest, LoginResponse, UsuarioResponse
from sqlalchemy.orm import Session
from app.core.seguridad import crear_token_acceso, crear_refresh_token
from app.services.auth_service import guardar_refresh_token
from app.BaseDeDatos import get_db


router = APIRouter()  # Crear un enrutador para los endpoints de autenticación

@router.post("/login", response_model=LoginResponse, tags=["Autenticación"])
async def login(credenciales: LoginRequest, db: Session = Depends(get_db)) -> LoginResponse:
    """
    Endpoint para autenticar a un usuario y generar un token de acceso.

    Args:
        credenciales (LoginRequest): Objeto que contiene el correo y la contraseña del usuario.
        db (Session): Sesión de la base de datos, inyectada automáticamente por FastAPI.

    Returns:
        LoginResponse: Objeto que contiene el token de acceso y su tipo.
    """
    # Llamar a la función de autenticación del servicio
    usuario = autenticar_usuario(credenciales.correo, credenciales.password, db)
    
    # Si la autenticación falla, lanzar una excepción HTTP 401
    if not usuario:
        raise HTTPException(status_code=401, detail="Correo o contraseña incorrectos")
    
    # Generar un token de acceso para el usuario autenticado
    access_token = crear_token_acceso(data={"sub": usuario.correo, "rol": usuario.rol})

    refresh_token, expiracion = crear_refresh_token() # Generar un token de refresco
    
    # Guardar el token de refresco en la base de datos
    guardar_refresh_token(usuario, refresh_token, expiracion, db)
    
    # Retornar el token de acceso en la respuesta
    return LoginResponse(
        access_token=access_token, 
        token_type="bearer", 
        refresh_token=refresh_token,
        usuario=UsuarioResponse.model_validate(usuario)
        )

@router.post("/refresh", response_model=LoginResponse, tags=["Autenticación"])
async def refresh_token(request: RefreshTokenRequest, db: Session = Depends(get_db)) -> LoginResponse:
    """
    Endpoint para refrescar el token de acceso utilizando un token de refresco válido.

    Args:
        request (RefreshTokenRequest): Objeto que contiene el token de refresco.
        db (Session): Sesión de la base de datos, inyectada automáticamente por FastAPI.

    Returns:
        LoginResponse: Objeto que contiene el nuevo token de acceso y su tipo.
    """
    # Verificar si el token de refresco es válido
    usuario = verificar_refresh_token(request.refresh_token, db)
    
    # Si el token de refresco no es válido, lanzar una excepción HTTP 401
    if not usuario:
        raise HTTPException(status_code=401, detail="Token de refresco inválido o expirado")
    
   # Generar nuevo access token
    access_token = crear_token_acceso(data={"sub": usuario.correo, "rol": usuario.rol})

    # Rotar el refresh token (generar uno nuevo)
    nuevo_refresh_token, expiracion = crear_refresh_token()
    guardar_refresh_token(usuario, nuevo_refresh_token, expiracion, db)

    return LoginResponse(
        access_token=access_token,
        token_type="bearer",
        refresh_token=nuevo_refresh_token,  # devuelve el nuevo
        usuario=UsuarioResponse.model_validate(usuario)
    )