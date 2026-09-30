from fastapi import APIRouter, Depends, HTTPException, Response, Query
from sqlalchemy.orm import Session, load_only
from math import ceil

from app.esquemas.orden_compra import OrdenCompraResumen, OrdenCompraActualizar, OrdenCompraListado
from app.BaseDeDatos import get_db
from app.core.dependencias import usuario_actual
from app.core.descargas import respuesta_archivo
from app.modelos.usuario import Usuarios
from app.services.detector_oc import clave_oc, nucleo_numerico
from app.modelos.factura import Facturas
from app.modelos.estados import Estados
from app.modelos.orden_compra import OrdenesCompra
from app.modelos.complemento_pago import ComplementosPago
from app.modelos.cp_documento_relacionado import CPDocumentosRelacionados
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo
from app.esquemas.factura import (
    FacturaListado, FacturaDetalle, FacturaActualizar,
    ConceptoDetalle, ComplementoResumen, VincularOCRequest,
    FiltrosFactura, ResumenFacturas, CancelarRequest, FacturaListadoConResumen,
    RespuestaVerificacionSat,
)
from app.services.verificacion_service import verificar_una
from app.services.factura_service import (
    contar_facturas_pendientes, reconciliar,
    cancelar_factura, cancelar_cp,
    marcar_revisada, revertir_revisada,
)
from app.services.query_builder import construir_query_facturas, calcular_resumen

router = APIRouter()

ZONA_MEXICO = ZoneInfo("America/Mexico_City")


# ─────────────────────────────────────────────
# HELPERS INTERNOS
# ─────────────────────────────────────────────

def _calcular_alerta(fecha_validacion: date | None, dias_plazo: int | None):
    """(fecha_limite, alerta, dias_restantes). Días negativos = vencida hace N días."""
    if not fecha_validacion or dias_plazo is None:
        return None, None, None
    fecha_limite = fecha_validacion + timedelta(days=dias_plazo)
    # Hoy en México: el VPS corre en UTC y de 18:00 a 23:59 date.today()
    # ya es "mañana" (mismo bug que la ventana del SAT, 25/09/2026).
    hoy = datetime.now(ZONA_MEXICO).date()
    dias_restantes = (fecha_limite - hoy).days
    if dias_restantes < 0:
        alerta = "vencida"
    elif dias_restantes <= 7:
        alerta = "por_vencer"
    else:
        alerta = "vigente"
    return fecha_limite, alerta, dias_restantes

def _factura_a_listado(f: Facturas, tiene_cp: bool) -> FacturaListado:
    fecha_limite, alerta, dias_restantes = _calcular_alerta(
        f.fecha_validacion, f.dias_plazo_pago_aplicado
    )
    # Una factura ya pagada no "vence": el contador deja de tener sentido.
    if f.fecha_liquidacion:
        alerta, dias_restantes = "pagada", None
    return FacturaListado(
        id_factura=f.id_factura,
        folio_fiscal=f.folio_fiscal,
        folio_interno=f.folio_interno,
        cliente=f.cliente,
        rfc=f.rfc,
        fecha=f.fecha,
        numero_oc=f.numero_oc,
        subtotal=f.subtotal,
        iva=f.iva,
        total=f.total,
        moneda=f.moneda,
        tipo_cambio=f.tipo_cambio,
        fecha_liquidacion=f.fecha_liquidacion,
        fecha_validacion=f.fecha_validacion,
        estado=f.estado.nombre_estado,
        tiene_pdf=f.pdf_factura is not None,
        tiene_xml=f.xml_factura is not None,
        tiene_oc=f.orden_compra_archivo is not None or f.id_orden_compra is not None,
        tiene_cp=tiene_cp,
        fecha_limite_pago=fecha_limite,
        alerta_vencimiento=alerta,
        dias_restantes=dias_restantes,
    )

def _obtener_usuario_actual(usuario: Usuarios) -> int:
    """
    Quién hizo el cambio, para HistorialVerificacion. Antes todo quedaba a
    nombre de "Sistema Automatico" porque la API no sabía quién llamaba.
    """
    return usuario.id_usuario


def _tiene_cp(db: Session, id_factura: int) -> bool:
    return (
        db.query(CPDocumentosRelacionados)
        .join(ComplementosPago,
              CPDocumentosRelacionados.id_complemento == ComplementosPago.id)
        .filter(
            CPDocumentosRelacionados.id_factura == id_factura,
            ComplementosPago.cancelado == False,   # noqa: E712
        )
        .first() is not None
    )


# ─────────────────────────────────────────────
# LISTADO PRINCIPAL  (búsqueda + filtros + resumen)
# ─────────────────────────────────────────────

@router.get("/", tags=["Facturas"])
def listar_facturas(
    filtros: FiltrosFactura = Depends(),
    db: Session = Depends(get_db),
) -> FacturaListadoConResumen:
    """
    Listado paginado con búsqueda, filtros y resumen de totales.

    Parámetros de query (todos opcionales):
      q               — búsqueda libre: cliente, UUID, folio_interno, numero_oc
      cliente         — filtro exacto por nombre de cliente (ilike)
      numero_oc       — filtro por número de OC (ilike)
      con_cp          — true/false: tiene o no CP vinculado
      incluir_canceladas — false por defecto
      fecha_desde     — YYYY-MM-DD
      fecha_hasta     — YYYY-MM-DD
      pagina          — default 1
      por_pagina      — default 50
    """
    q = construir_query_facturas(db, filtros)
    total_registros = q.count()
    total_paginas = ceil(total_registros / filtros.por_pagina) if total_registros else 1

    offset = (filtros.pagina - 1) * filtros.por_pagina
    facturas = q.order_by(Facturas.fecha.desc()).offset(offset).limit(filtros.por_pagina).all()

    resumen = calcular_resumen(db, filtros)

    resultado = [
        _factura_a_listado(f, _tiene_cp(db, f.id_factura))
        for f in facturas
    ]

    return FacturaListadoConResumen(
        facturas=resultado,
        resumen=resumen,
        pagina=filtros.pagina,
        por_pagina=filtros.por_pagina,
        total_paginas=total_paginas,
    )


# ─────────────────────────────────────────────
# RESUMEN STANDALONE  (para el dashboard sin listado)
# ─────────────────────────────────────────────

@router.get("/resumen", response_model=ResumenFacturas, tags=["Facturas"])
def obtener_resumen(
    filtros: FiltrosFactura = Depends(),
    db: Session = Depends(get_db),
):
    """Totales calculados en SQL sobre el mismo filtro del listado."""
    return calcular_resumen(db, filtros)


# ─────────────────────────────────────────────
# ESTADOS  (dropdown del dashboard)
# ─────────────────────────────────────────────

@router.get("/estados", tags=["Facturas"])
def listar_estados(db: Session = Depends(get_db)):
    estados = db.query(Estados).all()
    return [
        {
            "id_estado": e.id_estado,
            "nombre_estado": e.nombre_estado,
            "descripcion_estado": e.descripcion_estado,
        }
        for e in estados
    ]


# ─────────────────────────────────────────────
# PENDIENTES  
# ─────────────────────────────────────────────

@router.get("/pendientes/count", tags=["Facturas"])
def endpoint_contar_pendientes(db: Session = Depends(get_db)):
    return {"pendientes": contar_facturas_pendientes(db)}


# ─────────────────────────────────────────────
# DETALLE
# ─────────────────────────────────────────────

@router.get("/{id_factura}", response_model=FacturaDetalle, tags=["Facturas"])
def obtener_factura(id_factura: int, db: Session = Depends(get_db)):
    f = db.query(Facturas).filter(Facturas.id_factura == id_factura).first()
    if not f:
        raise HTTPException(status_code=404, detail="Factura no encontrada")

    conceptos = [
        ConceptoDetalle(
            descripcion=c.descripcion,
            cantidad=c.cantidad,
            unidad=c.unidad,
            precio_unitario=c.precio_unitario,
            importe=c.importe,
        )
        for c in f.conceptos
    ]

    # Una sola consulta con el CP adentro. Antes se traian los documentos y
    # luego se pedia el CP de cada uno por separado: con tres pagos parciales
    # eran cuatro idas a la base para una pantalla de detalle.
    docs = (
        db.query(CPDocumentosRelacionados, ComplementosPago)
        .join(ComplementosPago,
              CPDocumentosRelacionados.id_complemento == ComplementosPago.id)
        .filter(
            CPDocumentosRelacionados.id_factura == id_factura,
            ComplementosPago.cancelado == False,   # noqa: E712
        )
        .order_by(CPDocumentosRelacionados.num_parcialidad.asc().nullslast(),
                  ComplementosPago.fecha_pago.asc().nullslast())
        .all()
    )

    complementos = [
        ComplementoResumen(
            id=cp.id,
            uuid_cp=cp.uuid_cp,
            folio=cp.folio,
            fecha_pago=cp.fecha_pago,
            monto=cp.monto,
            imp_pagado=d.imp_pagado,
            imp_saldo_insoluto=d.imp_saldo_insoluto,
            num_parcialidad=d.num_parcialidad,
            liquida=d.imp_saldo_insoluto is not None and d.imp_saldo_insoluto == 0,
        )
        for d, cp in docs
    ]

    orden_compra = None
    if f.orden_compra:
        orden_compra = OrdenCompraResumen(
            id=f.orden_compra.id,
            numero_oc=f.orden_compra.numero_oc,
            numero_oc_detectado=f.orden_compra.numero_oc_detectado,
            nombre_archivo=f.orden_compra.nombre_archivo,
            fecha_recepcion=f.orden_compra.fecha_recepcion,
            tiene_archivo=f.orden_compra.archivo is not None,
        )

    return FacturaDetalle(
        id_factura=f.id_factura,
        folio_fiscal=f.folio_fiscal,
        folio_interno=f.folio_interno,
        cliente=f.cliente,
        rfc=f.rfc,
        fecha=f.fecha,
        numero_oc=f.numero_oc,
        numero_oc_detectado=f.numero_oc_detectado,
        subtotal=f.subtotal,
        iva=f.iva,
        total=f.total,
        moneda=f.moneda,
        tipo_cambio=f.tipo_cambio,
        fecha_liquidacion=f.fecha_liquidacion,
        fecha_validacion=f.fecha_validacion,
        estado=f.estado.nombre_estado,
        orden_compra=orden_compra,
        tiene_pdf=f.pdf_factura is not None,
        tiene_xml=f.xml_factura is not None,
        conceptos=conceptos,
        complementos=complementos,
    )


# ─────────────────────────────────────────────
# CORRECCIÓN MANUAL
# ─────────────────────────────────────────────

@router.patch("/{id_factura}", tags=["Facturas"])
def actualizar_factura(
    id_factura: int,
    datos: FacturaActualizar,
    db: Session = Depends(get_db),
    usuario: Usuarios = Depends(usuario_actual),
):
    """
    Guarda las correcciones manuales y, si la factura ya tiene OC + CP,
    la marca automáticamente como Revisada.
    """
    f = db.query(Facturas).filter(Facturas.id_factura == id_factura).first()
    if not f:
        raise HTTPException(status_code=404, detail="Factura no encontrada")

    if datos.folio_interno is not None:
        f.folio_interno = datos.folio_interno

    if datos.fecha_validacion is not None:
        f.fecha_validacion = datos.fecha_validacion
        if f.id_cliente:
            from app.modelos.cliente import Cliente
            cliente = db.query(Cliente).filter(Cliente.id == f.id_cliente).first()
            if cliente and cliente.dias_plazo_pago is not None:
                f.dias_plazo_pago_aplicado = cliente.dias_plazo_pago
        db.flush()

    if datos.numero_oc is not None and datos.numero_oc != f.numero_oc:
        estado_actual = f.estado.nombre_estado if f.estado else None
        f.numero_oc = datos.numero_oc
        f.id_orden_compra = None
        if estado_actual == "Revisada":
            db.commit()
            revertir_revisada(db, id_factura, _obtener_usuario_actual(usuario))
            f = db.query(Facturas).filter(Facturas.id_factura == id_factura).first()

    db.commit()

    # reconciliar() puede vincular la OC recién corregida
    reconciliar(db)

    # Solo después de reconciliar tiene sentido evaluar si está completa
    id_usuario = _obtener_usuario_actual(usuario)
    aplicada, motivo = marcar_revisada(db, id_factura, id_usuario)

    return {
        "factura": obtener_factura(id_factura, db),
        "revision": {"aplicada": aplicada, "motivo": motivo},
    }

@router.patch("/{id_factura}/revisar", tags=["Facturas"])
def marcar_revisada_endpoint(id_factura: int, db: Session = Depends(get_db),
                             usuario: Usuarios = Depends(usuario_actual)):
    id_usuario = _obtener_usuario_actual(usuario)
    aplicada, motivo = marcar_revisada(db, id_factura, id_usuario)
    if not aplicada:
        raise HTTPException(status_code=400, detail=motivo)
    return {"aplicada": True, "motivo": motivo}


@router.patch("/{id_factura}/revertir-revision", tags=["Facturas"])
def revertir_revision_endpoint(id_factura: int, db: Session = Depends(get_db),
                               usuario: Usuarios = Depends(usuario_actual)):
    id_usuario = _obtener_usuario_actual(usuario)
    aplicada, motivo = revertir_revisada(db, id_factura, id_usuario)
    if not aplicada:
        raise HTTPException(status_code=400, detail=motivo)
    return {"aplicada": True, "motivo": motivo}


# ─────────────────────────────────────────────
# CANCELACIÓN DE FACTURA
# ─────────────────────────────────────────────

@router.patch("/{id_factura}/cancelar", response_model=FacturaDetalle, tags=["Facturas"])
def cancelar_factura_endpoint(
    id_factura: int,
    datos: CancelarRequest,
    db: Session = Depends(get_db),
    usuario: Usuarios = Depends(usuario_actual),
):
    """
    Cambia el estado de la factura a 'Cancelada'.
    No borra nada de la BD. Registra el evento en HistorialVerificacion.
    """
    id_usuario = _obtener_usuario_actual(usuario)

    try:
        cancelar_factura(db, id_factura, datos.motivo, id_usuario)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    return obtener_factura(id_factura, db)

# ─────────────────────────────────────────────
# VERIFICACIÓN ANTE EL SAT
# ─────────────────────────────────────────────

@router.post(
    "/{id_factura}/verificar-sat",
    response_model=RespuestaVerificacionSat,
    tags=["Facturas"],
)
def verificar_sat_endpoint(id_factura: int, db: Session = Depends(get_db)):
    """
    Consulta el estatus del CFDI ante el SAT y sincroniza el estado local.

    Si el SAT reporta 'Cancelado' y la factura no lo está, cambia su estado
    a Cancelada y deja registro en HistorialVerificacion con origen='sat'.
    El SAT tiene autoridad sobre el estado local, incluso terminales.

    Puede tardar hasta 30 segundos: el webservice del SAT es lento.
    """
    resultado = verificar_una(db, id_factura, aplicar=True)

    if resultado is None:
        raise HTTPException(status_code=404, detail="Factura no encontrada")

    return resultado

# ─────────────────────────────────────────────
# CANCELACIÓN DE CP
# ─────────────────────────────────────────────

@router.patch("/cp/{id_cp}/cancelar", tags=["Facturas"])
def cancelar_cp_endpoint(
    id_cp: int,
    datos: CancelarRequest,
    db: Session = Depends(get_db),
    usuario: Usuarios = Depends(usuario_actual),
):
    """
    Cancela un CP administrativamente.
    Revierte fecha_liquidacion en las facturas que liquidó
    y llama a reconciliar() para recalcular sus estados.
    """
    id_usuario = _obtener_usuario_actual(usuario)

    try:
        cp = cancelar_cp(db, id_cp, datos.motivo, id_usuario)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    return {
        "id": cp.id,
        "uuid_cp": cp.uuid_cp,
        "cancelado": cp.cancelado,
        "fecha_cancelacion": cp.fecha_cancelacion,
        "motivo_cancelacion": cp.motivo_cancelacion,
    }


# ─────────────────────────────────────────────
# VINCULAR OC MANUAL
# ─────────────────────────────────────────────

@router.post("/{id_factura}/vincular-oc", response_model=FacturaDetalle, tags=["Facturas"])
def vincular_oc_manual(
    id_factura: int,
    datos: VincularOCRequest,
    db: Session = Depends(get_db),
):
    f = db.query(Facturas).filter(Facturas.id_factura == id_factura).first()
    if not f:
        raise HTTPException(status_code=404, detail="Factura no encontrada")

    oc = db.query(OrdenesCompra).filter(OrdenesCompra.id == datos.id_orden_compra).first()
    if not oc:
        raise HTTPException(status_code=404, detail="Orden de compra no encontrada")

    otras_facturas = db.query(Facturas).filter(
        Facturas.id_orden_compra == oc.id,
        Facturas.id_factura != id_factura,
    ).all()

    if otras_facturas and not datos.forzar:
        raise HTTPException(
            status_code=409,
            detail={
                "mensaje": "Esta orden de compra ya está vinculada a otra(s) factura(s).",
                "facturas_en_conflicto": [
                    {
                        "id_factura": otra.id_factura,
                        "folio_fiscal": otra.folio_fiscal,
                        "folio_interno": otra.folio_interno,
                    }
                    for otra in otras_facturas
                ],
            },
        )

    if otras_facturas and datos.forzar:
        for otra in otras_facturas:
            otra.id_orden_compra = None

    f.id_orden_compra = oc.id
    db.commit()
    reconciliar(db)
    return obtener_factura(id_factura, db)


# ─────────────────────────────────────────────
# OCS CANDIDATAS
# ─────────────────────────────────────────────

@router.get("/{id_factura}/ocs-candidatas", response_model=list[OrdenCompraListado], tags=["Facturas"])
def obtener_ocs_candidatas(id_factura: int, db: Session = Depends(get_db)):
    f = db.query(Facturas).filter(Facturas.id_factura == id_factura).first()
    if not f:
        raise HTTPException(status_code=404, detail="Factura no encontrada")

    if not f.numero_oc:
        return []

    # Por llave normalizada y, si no hay, por núcleo numérico: "BOS-507-91753."
    # en la factura encuentra la OC "BOS-507-91753"; "10003766965" encuentra
    # "C-10003766965". Es una lista para que el usuario elija, así que aquí
    # sí conviene ser generoso.
    llave = clave_oc(f.numero_oc)
    nucleo = nucleo_numerico(f.numero_oc)
    todas = db.query(OrdenesCompra).options(load_only(
        OrdenesCompra.id, OrdenesCompra.numero_oc, OrdenesCompra.numero_oc_detectado,
        OrdenesCompra.confianza_oc, OrdenesCompra.nombre_archivo, OrdenesCompra.fecha_recepcion,
    )).filter(OrdenesCompra.numero_oc.isnot(None)).all()
    ocs = [oc for oc in todas if clave_oc(oc.numero_oc) == llave]
    if not ocs and nucleo and len(nucleo) >= 5:
        ocs = [oc for oc in todas if nucleo_numerico(oc.numero_oc) == nucleo]

    con_archivo = {
        fila[0] for fila in db.query(OrdenesCompra.id).filter(
            OrdenesCompra.id.in_([oc.id for oc in ocs]), OrdenesCompra.archivo.isnot(None)
        ).all()
    } if ocs else set()

    return [
        OrdenCompraListado(
            id=oc.id,
            numero_oc=oc.numero_oc,
            numero_oc_detectado=oc.numero_oc_detectado,
            confianza_oc=oc.confianza_oc,
            nombre_archivo=oc.nombre_archivo,
            fecha_recepcion=oc.fecha_recepcion,
            tiene_archivo=oc.id in con_archivo,
            facturas_asociadas=db.query(Facturas)
                .filter(Facturas.id_orden_compra == oc.id)
                .count(),
        )
        for oc in ocs
    ]


# ─────────────────────────────────────────────
# ARCHIVOS
# ─────────────────────────────────────────────

@router.get("/{id_factura}/pdf", tags=["Facturas"])
def descargar_pdf(id_factura: int, db: Session = Depends(get_db)):
    f = db.query(Facturas).filter(Facturas.id_factura == id_factura).first()
    if not f or not f.pdf_factura:
        raise HTTPException(status_code=404, detail="PDF no disponible")

    return respuesta_archivo(f.pdf_factura, f.folio_interno, respaldo=f"factura_{f.id_factura}")


@router.get("/{id_factura}/xml", tags=["Facturas"])
def descargar_xml(id_factura: int, db: Session = Depends(get_db)):
    f = db.query(Facturas).filter(Facturas.id_factura == id_factura).first()
    if not f or not f.xml_factura:
        raise HTTPException(status_code=404, detail="XML no disponible")

    return respuesta_archivo(
        f.xml_factura, f.folio_interno, respaldo=f"factura_{f.id_factura}",
        media_type="application/xml", extension=".xml", inline=False,
    )
