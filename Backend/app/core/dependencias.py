"""
Dependencias de autenticacion para las rutas.

Por que existe
--------------
Hasta el 28/09/2026 la API emitia tokens en /auth/login y no los pedia en
ninguna parte: cualquiera con la URL podia leer las facturas del cliente o
disparar una operacion. La mas delicada es
POST /facturas-recibidas/sincronizacion, que hace que el servidor entre al
portal del SAT con la e.firma real de Monsort.

Uso:

    from app.core.dependencias import usuario_actual, requiere_roles

    @router.get("/algo")
    def algo(usuario: Usuarios = Depends(usuario_actual)):
        ...

    @router.post("/algo-delicado")
    def algo_delicado(usuario: Usuarios = Depends(requiere_roles(*ROLES_ADMIN))):
        ...

El rol se lee de la BASE, no del token. Un token emitido antes de bajarle el
rol a alguien seguiria diciendo el rol viejo hasta expirar; la fila del
usuario es la verdad del momento.
"""
import logging

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.BaseDeDatos import get_db
from app.core.seguridad import decode_token
from app.modelos.usuario import Usuarios

logger = logging.getLogger(__name__)

# Los mismos que el frontend usa para mostrar "+ Crear usuario".
ROLES_ADMIN = ("desarrollador", "administrador")

# auto_error=False para devolver un 401 con mensaje propio en vez del 403
# generico de FastAPI cuando falta el encabezado.
esquema_bearer = HTTPBearer(auto_error=False)

NO_AUTORIZADO = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Sesion invalida o expirada. Vuelve a iniciar sesion.",
    headers={"WWW-Authenticate": "Bearer"},
)


def usuario_actual(
    credenciales: HTTPAuthorizationCredentials | None = Depends(esquema_bearer),
    db: Session = Depends(get_db),
) -> Usuarios:
    """El usuario dueño del token, o 401."""
    if credenciales is None or not credenciales.credentials:
        raise NO_AUTORIZADO

    datos = decode_token(credenciales.credentials)
    if not datos:
        # decode_token devuelve None tanto para un token vencido como para uno
        # alterado. Para el cliente son el mismo caso: iniciar sesion de nuevo.
        raise NO_AUTORIZADO

    correo = datos.get("sub")
    if not correo:
        raise NO_AUTORIZADO

    usuario = db.query(Usuarios).filter(Usuarios.correo == correo).first()
    if not usuario:
        # El token es valido pero el usuario ya no existe.
        logger.warning("Token valido de un usuario inexistente: %s", correo)
        raise NO_AUTORIZADO

    return usuario


def requiere_roles(*roles: str):
    """
    Dependencia que ademas exige uno de estos roles.

    Devuelve 403 y no 401: el usuario esta identificado, lo que falta es el
    permiso. Distinguirlos importa para el frontend — un 401 debe mandar al
    login y un 403 no.
    """
    permitidos = {r.lower() for r in roles}

    def verificar(usuario: Usuarios = Depends(usuario_actual)) -> Usuarios:
        if (usuario.rol or "").lower() not in permitidos:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Tu cuenta no tiene permiso para esta operacion",
            )
        return usuario

    return verificar
