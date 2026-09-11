from math import ceil

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.BaseDeDatos import get_db
from app.modelos.facturas_recibidas import FacturasRecibidas
from app.esquemas.factura_recibida import (
    FacturaRecibidaListado,
    FacturaRecibidaDetalle,
    FacturaRecibidaListadoConResumen,
    FiltrosFacturaRecibida,
    ResumenFacturasRecibidas,
    EmisorResumen,
    SincronizarRequest,
)
from app.services.query_builder_recibidas import (
    construir_query_facturas_recibidas,
    calcular_resumen_recibidas,
    listar_emisores,
)

router = APIRouter()


# ─────────────────────────────────────────────
# LISTADO PRINCIPAL
# ─────────────────────────────────────────────

@router.get("/", tags=["Facturas recibidas"])
def listar_facturas_recibidas(
    filtros: FiltrosFacturaRecibida = Depends(),
    db: Session = Depends(get_db),
) -> FacturaRecibidaListadoConResumen:
    """
    CFDI donde Monsort es RECEPTOR (lo que le facturan sus proveedores).
    No confundir con /facturas, que son las emitidas por Monsort.

    Parametros de query (todos opcionales):
      q                   — busqueda libre: folio fiscal, RFC o nombre del emisor
      rfc_emisor          — ilike
      nombre_emisor       — ilike
      sat_estado          — "Vigente" | "Cancelado"
      solo_facturas       — true por defecto: filtra a EfectoComprobante = I
      efecto_comprobante  — I | E | T | N | P (gana sobre solo_facturas)
      fecha_desde         — YYYY-MM-DD
      fecha_hasta         — YYYY-MM-DD (inclusivo)
      monto_min           — decimal
      monto_max           — decimal
      pagina              — default 1
      por_pagina          — default 50
    """
    q = construir_query_facturas_recibidas(db, filtros)
    total_registros = q.count()
    total_paginas = ceil(total_registros / filtros.por_pagina) if total_registros else 1

    offset = (filtros.pagina - 1) * filtros.por_pagina
    facturas = (
        q.order_by(FacturasRecibidas.fecha_emision.desc())
        .offset(offset)
        .limit(filtros.por_pagina)
        .all()
    )

    return FacturaRecibidaListadoConResumen(
        facturas=[FacturaRecibidaListado.model_validate(f) for f in facturas],
        resumen=calcular_resumen_recibidas(db, filtros),
        pagina=filtros.pagina,
        por_pagina=filtros.por_pagina,
        total_paginas=total_paginas,
    )


# ─────────────────────────────────────────────
# RESUMEN STANDALONE
# ─────────────────────────────────────────────

@router.get("/resumen", response_model=ResumenFacturasRecibidas,
            tags=["Facturas recibidas"])
def obtener_resumen_recibidas(
    filtros: FiltrosFacturaRecibida = Depends(),
    db: Session = Depends(get_db),
):
    """Totales calculados en SQL sobre el mismo filtro del listado."""
    return calcular_resumen_recibidas(db, filtros)


# ─────────────────────────────────────────────
# EMISORES  (dropdown del dashboard)
# ─────────────────────────────────────────────
#
# IMPORTANTE: esta ruta va ANTES de /{id_factura_recibida}. FastAPI
# resuelve en orden de declaracion; si estuviera despues, "emisores" se
# interpretaria como un id y devolveria 422.

@router.get("/emisores", response_model=list[EmisorResumen],
            tags=["Facturas recibidas"])
def obtener_emisores(db: Session = Depends(get_db)):
    """Proveedores distintos con su conteo de facturas."""
    return listar_emisores(db)


# ─────────────────────────────────────────────
# SINCRONIZACION MANUAL
# ─────────────────────────────────────────────

@router.post("/sincronizar", tags=["Facturas recibidas"])
def sincronizar_recibidas(
    datos: SincronizarRequest,
    db: Session = Depends(get_db),
):
    """
    Registra una solicitud de descarga masiva para el rango indicado.

    NO descarga nada de inmediato: el servicio del SAT es asincrono y
    puede tardar de minutos a horas. La solicitud queda en estado NUEVA
    y el job de descarga masiva la va avanzando cada 10 minutos.
    """
    from app.services.descarga_masiva_service import crear_solicitud
    from app.services.sat_descarga_client import (
        TIPO_METADATA, COMPROBANTE_RECIBIDOS,
    )

    if datos.fecha_inicial > datos.fecha_final:
        raise HTTPException(
            status_code=400,
            detail="fecha_inicial no puede ser mayor que fecha_final",
        )

    solicitud = crear_solicitud(
        db,
        datos.fecha_inicial,
        datos.fecha_final,
        TIPO_METADATA,
        COMPROBANTE_RECIBIDOS,
    )

    return {
        "id_solicitud": solicitud.id,
        "estado": solicitud.estado,
        "fecha_inicial": solicitud.fecha_inicial,
        "fecha_final": solicitud.fecha_final,
        "mensaje": (
            "Solicitud registrada. El SAT puede tardar de minutos a horas; "
            "el sistema la avanza automaticamente."
        ),
    }


# ─────────────────────────────────────────────
# DETALLE
# ─────────────────────────────────────────────

@router.get("/{id_factura_recibida}", response_model=FacturaRecibidaDetalle,
            tags=["Facturas recibidas"])
def obtener_factura_recibida(
    id_factura_recibida: int,
    db: Session = Depends(get_db),
):
    f = (
        db.query(FacturasRecibidas)
        .filter(FacturasRecibidas.id_factura_recibida == id_factura_recibida)
        .first()
    )
    if not f:
        raise HTTPException(status_code=404, detail="Factura recibida no encontrada")

    return FacturaRecibidaDetalle.model_validate(f)