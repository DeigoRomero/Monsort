import logging
from datetime import datetime, timedelta, timezone
from math import ceil

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Response
from sqlalchemy.orm import Session, undefer

from app.BaseDeDatos import get_db
from app.core.dependencias import ROLES_ADMIN, requiere_roles
from app.modelos.facturas_recibidas import FacturasRecibidas
from app.modelos.usuario import Usuarios
from app.esquemas.factura_recibida import (
    FacturaRecibidaListado,
    FacturaRecibidaDetalle,
    FacturaRecibidaListadoConResumen,
    FiltrosFacturaRecibida,
    ResumenFacturasRecibidas,
    EmisorResumen,
    SincronizarRequest,
    CorridaPortal,
    EstadoSincronizacionPortal,
    SincronizarPortalRequest,
)
from app.services.query_builder_recibidas import (
    construir_query_facturas_recibidas,
    calcular_resumen_recibidas,
    listar_emisores,
)

logger = logging.getLogger(__name__)

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
# SINCRONIZACION CON EL PORTAL DEL SAT
# ─────────────────────────────────────────────
#
# Van ANTES de /{id_factura_recibida} por la misma razon que /emisores.

# Una manual cada 10 minutos basta; cada corrida inicia sesion en el SAT
# con la e.firma y no conviene que un doble clic dispare varias.
MINUTOS_ENTRE_MANUALES = 10


@router.get("/sincronizacion", response_model=EstadoSincronizacionPortal,
            tags=["Facturas recibidas"])
def obtener_estado_sincronizacion(db: Session = Depends(get_db)):
    """
    Estado de la sincronizacion con el portal del SAT.

    Para el encabezado de la tabla: "Ultima sincronizacion: hoy 13:02 ·
    7 nuevas en 24 h". Si `alerta` trae texto, mostrarlo tal cual.
    """
    from app.services.portal_sat_service import estado_sincronizacion
    return estado_sincronizacion(db)


@router.get("/sincronizacion/historial", response_model=list[CorridaPortal],
            tags=["Facturas recibidas"])
def historial_sincronizacion(limite: int = 20, db: Session = Depends(get_db)):
    """Ultimas corridas contra el portal, la mas reciente primero."""
    from app.modelos.sincronizacion_portal import SincronizacionesPortal
    from app.services.portal_sat_service import resumen_corrida

    limite = max(1, min(limite, 100))
    corridas = (
        db.query(SincronizacionesPortal)
        .order_by(SincronizacionesPortal.inicio.desc())
        .limit(limite)
        .all()
    )
    return [resumen_corrida(c) for c in corridas]


def _correr_sincronizacion_manual(dias: int) -> None:
    """Se ejecuta en segundo plano, con su propia sesion de base."""
    from app.BaseDeDatos import SessionLocal
    from app.services.portal_sat_service import (
        MOTIVO_MANUAL, SincronizacionEnCurso, hoy_mexico, sincronizar_recibidas,
    )

    db = SessionLocal()
    try:
        hoy = hoy_mexico()
        sincronizar_recibidas(db, hoy - timedelta(days=dias), hoy, MOTIVO_MANUAL)
    except SincronizacionEnCurso:
        pass
    finally:
        db.close()


@router.post("/sincronizacion", status_code=202, tags=["Facturas recibidas"])
def sincronizar_con_portal(
    datos: SincronizarPortalRequest,
    tareas: BackgroundTasks,
    db: Session = Depends(get_db),
    usuario: Usuarios = Depends(requiere_roles(*ROLES_ADMIN)),
):
    """
    Consulta el portal del SAT AHORA, sin esperar al horario programado.

    Responde de inmediato (202) y la consulta corre en segundo plano; tarda
    de segundos a un par de minutos. El resultado se ve en
    GET /facturas-recibidas/sincronizacion.

    Requiere sesion y rol de administrador: esta llamada hace que el servidor
    inicie sesion en el portal del SAT con la e.firma de Monsort. Es la
    operacion mas delicada de la API — no puede quedar abierta a cualquiera
    que conozca la URL.
    """
    from app.core.config import settings
    from app.modelos.sincronizacion_portal import SincronizacionesPortal
    from app.services.portal_sat_service import EN_CURSO, MOTIVO_MANUAL

    if not settings.SAT_PORTAL_ACTIVO:
        raise HTTPException(status_code=409, detail="La sincronizacion con el portal del SAT no esta activada")
    if not 1 <= datos.dias <= 90:
        raise HTTPException(status_code=400, detail="dias debe estar entre 1 y 90")

    ultima = db.query(SincronizacionesPortal).order_by(SincronizacionesPortal.inicio.desc()).first()
    if ultima and ultima.estado == EN_CURSO:
        raise HTTPException(status_code=409, detail="Ya hay una sincronizacion en curso")

    ultima_manual = (
        db.query(SincronizacionesPortal)
        .filter(SincronizacionesPortal.motivo == MOTIVO_MANUAL)
        .order_by(SincronizacionesPortal.inicio.desc())
        .first()
    )
    if ultima_manual and datetime.now(timezone.utc) - ultima_manual.inicio < timedelta(minutes=MINUTOS_ENTRE_MANUALES):
        raise HTTPException(
            status_code=429,
            detail=f"Espera {MINUTOS_ENTRE_MANUALES} minutos entre sincronizaciones manuales",
        )

    # Queda asentado quien la disparo: si el SAT bloquea la cuenta por
    # exceso de accesos, la bitacora dice de donde vinieron.
    logger.info(
        "Sincronizacion manual con el portal del SAT pedida por %s (%s), %d dia(s)",
        usuario.correo, usuario.rol, datos.dias,
    )

    tareas.add_task(_correr_sincronizacion_manual, datos.dias)
    return {"mensaje": "Sincronizacion iniciada. Consulta el estado en unos minutos.", "dias": datos.dias}


# ─────────────────────────────────────────────
# SINCRONIZACION MANUAL
# ─────────────────────────────────────────────

@router.post("/sincronizar", tags=["Facturas recibidas"])
def sincronizar_recibidas(
    datos: SincronizarRequest,
    db: Session = Depends(get_db),
    usuario: Usuarios = Depends(requiere_roles(*ROLES_ADMIN)),
):
    """
    Registra una solicitud de descarga masiva para el rango indicado.

    Protegida por lo mismo que la del portal: la solicitud se firma con la
    e.firma, y las peticiones de tipo `cfdi` tienen limite de por vida por
    periodo. Que un tercero las gaste no se puede deshacer.

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


@router.get("/{id_factura_recibida}/xml", tags=["Facturas recibidas"])
def descargar_xml_recibida(
    id_factura_recibida: int,
    db: Session = Depends(get_db),
):
    """Descarga el XML del CFDI (solo si ya se bajo del portal del SAT)."""
    f = (
        db.query(FacturasRecibidas)
        .options(undefer(FacturasRecibidas.xml_factura))
        .filter(FacturasRecibidas.id_factura_recibida == id_factura_recibida)
        .first()
    )
    if not f:
        raise HTTPException(status_code=404, detail="Factura recibida no encontrada")
    if not f.xml_factura:
        raise HTTPException(status_code=404, detail="Esta factura todavia no tiene XML")

    return Response(
        content=f.xml_factura,
        media_type="application/xml",
        headers={"Content-Disposition": f'attachment; filename="{f.folio_fiscal}.xml"'},
    )
