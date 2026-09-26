from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.rutas.health import router as EstadoRouter
from app.api.rutas.auth import router as AuthRouter
from app.api.rutas.facturas import router as FacturaRouter
from app.api.rutas.ordenes_compra import router as OrdenCompraRouter
from app.api.rutas.reportes import router as ReporteRouter
from app.api.rutas.clientes import router as ClienteRouter
from app.api.rutas.auth_gmail import router as AuthGmailRouter
from app.api.rutas.facturas_recibidas import router as FacturaRecibidaRouter
from app.api.rutas.complementos import router as ComplementoRouter
from app.modelos import usuario, factura, estados
from app.core.scheduler import scheduler
from app.core.config import Settings

# Una sola instancia: aquí es donde se lee el .env
settings = Settings()

origenes = [
    o.strip().rstrip("/")
    for o in settings.CORS_ORIGINS.split(",")
    if o.strip()
]

regex_desarrollo = (
    r"^http://(localhost|127\.0\.0\.1)(:\d+)?$"
    if settings.ENTORNO == "desarrollo"
    else None
)

# TEMPORAL: confirma qué cargó el servidor. Bórralo cuando funcione.
print(">>> CORS origenes:", repr(origenes), "| ENTORNO:", repr(settings.ENTORNO))


@asynccontextmanager
async def lifespan(app: FastAPI):
    scheduler.start()
    print("Scheduler started")
    yield
    scheduler.shutdown()


aplicacion = FastAPI(lifespan=lifespan)

aplicacion.add_middleware(
    CORSMiddleware,
    allow_origins=origenes,
    allow_origin_regex=regex_desarrollo,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

aplicacion.include_router(FacturaRouter, prefix="/facturas", tags=["Facturas"])
aplicacion.include_router(EstadoRouter, prefix="/health", tags=["Health"])
aplicacion.include_router(AuthRouter, prefix="/auth", tags=["Autenticación"])
aplicacion.include_router(OrdenCompraRouter, prefix="/ordenes-compra", tags=["Ordenes de compra"])
aplicacion.include_router(ReporteRouter, prefix="/reportes", tags=["Reportes"])
aplicacion.include_router(ClienteRouter, prefix="/clientes", tags=["Clientes"])
aplicacion.include_router(AuthGmailRouter, prefix="/auth", tags=["Autenticación Gmail"])
aplicacion.include_router(FacturaRecibidaRouter, prefix="/facturas-recibidas", tags=["Facturas recibidas"])
aplicacion.include_router(ComplementoRouter, prefix="/complementos", tags=["Complementos de pago"])