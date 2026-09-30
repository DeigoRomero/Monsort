import logging
# Dos endpoints de reportes PDF:
#   POST /reportes/general     → reporte general filtrado
#   GET  /reportes/detalle/{id} → reporte por factura

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.orm import Session
from datetime import datetime

from app.BaseDeDatos import get_db
from app.core.descargas import respuesta_archivo
from app.esquemas.factura import FiltrosFactura
from app.services.reporte_service import generar_reporte_general, generar_reporte_detalle
from app.esquemas.factura_recibida import FiltrosFacturaRecibida
from app.services.reporte_recibidas_service import generar_reporte_recibidas
router = APIRouter()
logger = logging.getLogger(__name__)


@router.get("/general", tags=["Reportes"])
def reporte_general(
    filtros: FiltrosFactura = Depends(),
    solo_ciclo_completo: bool = False,
    db: Session = Depends(get_db),
):
    """
    Reporte general de facturas en PDF.
    Acepta los mismos query params que GET /facturas/:
      cliente, fecha_desde, fecha_hasta, numero_oc, q, con_cp

    Devuelve lo mismo que devolveria el listado con esos filtros, incluido el
    historico importado del Excel. Antes del 29/09/2026 se quedaba solo con
    las facturas que tuvieran orden de compra Y complemento de pago, sin que
    nada en la pantalla lo dijera: por eso un rango de fechas parecia no
    funcionar.

    solo_ciclo_completo=true recupera ese comportamiento, ahora explicito.

    Columnas: Importe (subtotal del CFDI), IVA y Total, mas Importe MXN con la
    conversion por tipo de cambio. El total facturado se suma sobre el importe.
    """
    try:
        pdf_bytes = generar_reporte_general(db, filtros, solo_ciclo_completo)
    except Exception:
        # La traza va al log; el mensaje al cliente no revela internos.
        logger.exception("Error generando reporte")
        raise HTTPException(status_code=500, detail="No se pudo generar el reporte")

    nombre = f"reporte_general_{datetime.now().strftime('%Y%m%d_%H%M')}.pdf"
    return respuesta_archivo(pdf_bytes, nombre, respaldo="reporte", inline=False)


@router.get("/detalle/{id_factura}", tags=["Reportes"])
def reporte_detalle(
    id_factura: int,
    db: Session = Depends(get_db),
):
    """
    Reporte detallado de una factura en PDF.
    Incluye secciones: Factura, Orden de Compra, Complemento(s) de Pago.
    """
    try:
        pdf_bytes = generar_reporte_detalle(db, id_factura)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception:
        # La traza va al log; el mensaje al cliente no revela internos.
        logger.exception("Error generando reporte")
        raise HTTPException(status_code=500, detail="No se pudo generar el reporte")

    nombre = f"factura_{id_factura}_detalle_{datetime.now().strftime('%Y%m%d_%H%M')}.pdf"
    return respuesta_archivo(pdf_bytes, nombre, respaldo="reporte", inline=False)

@router.get("/recibidas", tags=["Reportes"])
def reporte_recibidas(
    filtros: FiltrosFacturaRecibida = Depends(),
    db: Session = Depends(get_db),
):
    """
    Reporte de CFDI recibidos (proveedores), agrupado por proveedor.
 
    Acepta los mismos query params que GET /facturas-recibidas/:
      q, rfc_emisor, nombre_emisor, sat_estado, solo_facturas,
      efecto_comprobante, fecha_desde, fecha_hasta, monto_min, monto_max
 
    La paginacion se ignora: el reporte incluye todo el conjunto filtrado.
 
    Las canceladas aparecen marcadas en rojo pero NO suman a los totales.
    """
    try:
        pdf_bytes = generar_reporte_recibidas(db, filtros)
    except Exception:
        # La traza va al log; el mensaje al cliente no revela internos.
        logger.exception("Error generando reporte")
        raise HTTPException(status_code=500, detail="No se pudo generar el reporte")
 
    nombre = f"reporte_recibidas_{datetime.now().strftime('%Y%m%d_%H%M')}.pdf"
    return respuesta_archivo(pdf_bytes, nombre, respaldo="reporte", inline=False)