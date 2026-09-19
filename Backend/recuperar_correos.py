import logging
logging.basicConfig(level=logging.INFO, format="%(levelname)-8s %(message)s")

from app.modelos import (  # noqa: F401
    usuario, factura, estados, configuracion, conceptos,
    complemento_pago, orden_compra, cp_documento_relacionado,
    correo_procesado, cliente, solicitud_sat,
)
from app.BaseDeDatos import SessionLocal
from app.services.gmail_service import obtener_servicio_gmail, extraer_adjuntos
from app.services.factura_service import procesar_correo
from app.services.usuario_service import obtener_usuario_sistema

db = SessionLocal()
servicio = obtener_servicio_gmail(db)
usuario = obtener_usuario_sistema(db)

mid = "1a0a15b0dd28f2ab"
adjuntos, asunto = extraer_adjuntos(servicio, mid)
resumen = procesar_correo(adjuntos, asunto, mid, db, usuario)
db.rollback()          # dry-run: no guardar nada
print(f"\nResumen: {resumen}")
db.close()