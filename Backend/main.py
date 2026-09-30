import logging
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import JSONResponse

from app.api.rutas.health import router as EstadoRouter
from app.api.rutas.auth import router as AuthRouter
from app.api.rutas.facturas import router as FacturaRouter
from app.api.rutas.ordenes_compra import router as OrdenCompraRouter
from app.api.rutas.reportes import router as ReporteRouter
from app.api.rutas.clientes import router as ClienteRouter
from app.api.rutas.auth_gmail import router as AuthGmailRouter
from app.api.rutas.facturas_recibidas import router as FacturaRecibidaRouter
from app.api.rutas.complementos import router as ComplementoRouter
from app.api.rutas.sat import router as SatRouter
from app.api.rutas.notificaciones import router as NotificacionRouter
from app.core.dependencias import usuario_actual
from app.core.scheduler import scheduler
from app.core.config import Settings
from app.modelos import usuario, factura, estados  # noqa: F401  (registra modelos)

# ---------------------------------------------------------------------------
# Logging de la aplicación
#
# Sin esto, TODO logger.info del proyecto se descarta en produccion. Un logger
# sin nivel propio hereda del root, cuyo default es WARNING, y el
# --log-level info de uvicorn solo configura los loggers de uvicorn.
# Se configura el logger "app" y no el root: todos los modulos del proyecto
# usan logging.getLogger(__name__), que bajo app/ da "app.services.x".
# "app.seguridad" (logins, bloqueos, cambios de contraseña) cuelga de aquí.
_logger_app = logging.getLogger("app")
if not _logger_app.handlers:
    _manejador = logging.StreamHandler()
    _manejador.setFormatter(
        logging.Formatter("%(levelname)-8s %(name)s: %(message)s")
    )
    _logger_app.addHandler(_manejador)
_logger_app.setLevel(logging.INFO)
_logger_app.propagate = False
logger = logging.getLogger("app.main")

# Una sola instancia: aquí es donde se lee el .env
settings = Settings()
ES_PRODUCCION = settings.ENTORNO != "desarrollo"

if len(settings.SECRET_KEY or "") < 32:
    # Con una llave corta se pueden adivinar tokens por fuerza bruta offline.
    logger.critical(
        "SECRET_KEY tiene menos de 32 caracteres. Genera una con: "
        "python -c \"import secrets; print(secrets.token_urlsafe(64))\""
    )

origenes = [
    o.strip().rstrip("/")
    for o in settings.CORS_ORIGINS.split(",")
    if o.strip()
]

regex_desarrollo = (
    r"^http://(localhost|127\.0\.0\.1)(:\d+)?$"
    if not ES_PRODUCCION
    else None
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    scheduler.start()
    logger.info("Scheduler iniciado (ENTORNO=%s, CORS=%s)", settings.ENTORNO, origenes)
    yield
    scheduler.shutdown()


aplicacion = FastAPI(
    lifespan=lifespan,
    title="Monsort API",
    # La documentación interactiva describe cada ruta y cada parámetro: útil
    # en desarrollo, un mapa para un atacante en producción.
    docs_url=None if ES_PRODUCCION else "/docs",
    redoc_url=None if ES_PRODUCCION else "/redoc",
    openapi_url=None if ES_PRODUCCION else "/openapi.json",
)


# ---------------------------------------------------------------------------
# Encabezados de seguridad en TODAS las respuestas de la API.
# nginx pone los suyos para el frontend; estos cubren la API aunque alguien
# la llame directo al puerto 8000 o cambie la configuración de nginx.
# ---------------------------------------------------------------------------
ENCABEZADOS_SEGURIDAD = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=(), usb=()",
    "Cross-Origin-Opener-Policy": "same-origin",
    "Cross-Origin-Resource-Policy": "same-site",
    # La API no sirve páginas con scripts: nada puede cargarse ni enmarcarse.
    "Content-Security-Policy": "default-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'",
}


@aplicacion.middleware("http")
async def encabezados_seguridad(request: Request, call_next):
    respuesta = await call_next(request)
    for nombre, valor in ENCABEZADOS_SEGURIDAD.items():
        respuesta.headers.setdefault(nombre, valor)
    if ES_PRODUCCION:
        respuesta.headers.setdefault(
            "Strict-Transport-Security", "max-age=63072000; includeSubDomains"
        )
    # Datos fiscales y tokens: que no se guarden en cachés del navegador ni
    # de proxies. Las descargas ya traen su propio Cache-Control.
    respuesta.headers.setdefault("Cache-Control", "no-store")
    respuesta.headers["Server"] = "monsort"
    return respuesta


@aplicacion.exception_handler(Exception)
async def error_no_controlado(request: Request, exc: Exception):
    # La traza va al log; al cliente nunca (revela rutas, versiones, SQL).
    logger.exception("Error no controlado en %s %s", request.method, request.url.path)
    return JSONResponse(status_code=500, content={"detail": "Error interno del servidor"})


aplicacion.add_middleware(
    CORSMiddleware,
    allow_origins=origenes,
    allow_origin_regex=regex_desarrollo,
    allow_credentials=True,   # la cookie del refresh token
    allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-Requested-With",
                   "ngrok-skip-browser-warning"],
    expose_headers=["Content-Disposition", "Retry-After"],
    max_age=600,
)

if ES_PRODUCCION and settings.HOSTS_PERMITIDOS.strip() != "*":
    aplicacion.add_middleware(
        TrustedHostMiddleware,
        allowed_hosts=[h.strip() for h in settings.HOSTS_PERMITIDOS.split(",") if h.strip()],
    )

# ---------------------------------------------------------------------------
# Rutas. TODO exige sesión salvo /health, /auth (login, refresh, logout;
# las demás rutas de /auth piden sesión o rol ellas mismas) y el callback
# de Google, que se valida con el 'state' firmado.
# ---------------------------------------------------------------------------
con_sesion = [Depends(usuario_actual)]

aplicacion.include_router(EstadoRouter, prefix="/health", tags=["Health"])
aplicacion.include_router(AuthRouter, prefix="/auth", tags=["Autenticación"])
aplicacion.include_router(AuthGmailRouter, prefix="/auth", tags=["Autenticación Gmail"])

aplicacion.include_router(FacturaRouter, prefix="/facturas", tags=["Facturas"], dependencies=con_sesion)
aplicacion.include_router(OrdenCompraRouter, prefix="/ordenes-compra", tags=["Ordenes de compra"], dependencies=con_sesion)
aplicacion.include_router(ReporteRouter, prefix="/reportes", tags=["Reportes"], dependencies=con_sesion)
aplicacion.include_router(ClienteRouter, prefix="/clientes", tags=["Clientes"], dependencies=con_sesion)
aplicacion.include_router(FacturaRecibidaRouter, prefix="/facturas-recibidas", tags=["Facturas recibidas"], dependencies=con_sesion)
aplicacion.include_router(ComplementoRouter, prefix="/complementos", tags=["Complementos de pago"], dependencies=con_sesion)
aplicacion.include_router(SatRouter, prefix="/sat", tags=["Reconciliación SAT"], dependencies=con_sesion)
aplicacion.include_router(NotificacionRouter, prefix="/notificaciones", tags=["Notificaciones"], dependencies=con_sesion)
