from fastapi import FastAPI
from app.api.rutas.health import router as EstadoRouter
from app.api.rutas.auth import router as AuthRouter
from app.api.rutas.facturas import router as FacturaRouter
from app.modelos import usuario, factura, estados
from fastapi.middleware.cors import CORSMiddleware
from app.core.scheduler import scheduler
from contextlib import asynccontextmanager

origenes = [
    "https://*.ngrok.io",
    "http://localhost:5173"
]

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
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"]
)

aplicacion.include_router(FacturaRouter, prefix="/facturas", tags=["Facturas"])
aplicacion.include_router(EstadoRouter, prefix="/health", tags=["Health"])
aplicacion.include_router(AuthRouter, prefix="/auth", tags=["Autenticación"])