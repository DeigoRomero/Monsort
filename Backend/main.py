from fastapi import FastAPI
from app.api.rutas.health import router as EstadoRouter
from app.api.rutas.auth import router as AuthRouter
from app.modelos import usuario, factura, estados
from fastapi.middleware.cors import CORSMiddleware

origenes = [
    "https://*.ngrok.io",
    "http://localhost:5173"
]
aplicacion = FastAPI()

aplicacion.add_middleware(
    CORSMiddleware,
    allow_origins=origenes,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"]
)

aplicacion.include_router(EstadoRouter, prefix="/health", tags=["Health"])
aplicacion.include_router(AuthRouter, prefix="/auth", tags=["Autenticación"])