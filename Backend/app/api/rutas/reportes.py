# Dos endpoints de reportes PDF:
#   POST /reportes/general     → reporte general filtrado
#   GET  /reportes/detalle/{id} → reporte por factura

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.orm import Session
from datetime import datetime

from app.BaseDeDatos import get_db
from app.esquemas.factura import FiltrosFactura
from app.services.reporte_service import generar_reporte_general, generar_reporte_detalle

router = APIRouter()


@router.get("/general", tags=["Reportes"])
def reporte_general(
    filtros: FiltrosFactura = Depends(),
    db: Session = Depends(get_db),
):
    """
    Reporte general de facturas en PDF.
    Acepta los mismos query params que GET /facturas/:
      cliente, fecha_desde, fecha_hasta, numero_oc, q, con_cp

    Solo incluye facturas con ciclo completo (OC + CP activo).
    Convierte totales a MXN con el tipo_cambio del CFDI.
    """
    try:
        pdf_bytes = generar_reporte_general(db, filtros)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error generando reporte: {str(e)}")

    nombre = f"reporte_general_{datetime.now().strftime('%Y%m%d_%H%M')}.pdf"
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{nombre}"'},
    )


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
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error generando reporte: {str(e)}")

    nombre = f"factura_{id_factura}_detalle_{datetime.now().strftime('%Y%m%d_%H%M')}.pdf"
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{nombre}"'},
    )