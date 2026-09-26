"""
Endpoints de complementos de pago.

Existen porque hasta el 26/09/2026 no había ninguno: los CPs entraban a la
base y desde la interfaz eran invisibles. `/facturas/{id}` los mostraba
anidados dentro de una factura, así que un CP que no se pudo pegar a ninguna
factura no aparecía en ninguna parte. El cliente concluyó, con razón, que la
página no los estaba guardando.

Las rutas estáticas van ANTES de /{id_cp}: con /{id_cp} declarada primero,
FastAPI intenta parsear "resumen" como int y devuelve 422.
"""

from fastapi import APIRouter, Depends, HTTPException, Response, Query
from sqlalchemy import func, or_, desc
from sqlalchemy.orm import Session
from math import ceil
from decimal import Decimal

from app.BaseDeDatos import get_db
from app.modelos.complemento_pago import ComplementosPago
from app.modelos.cp_documento_relacionado import CPDocumentosRelacionados
from app.modelos.correo_procesado import CorreosProcesados, CorreosFallidos
from app.modelos.factura import Facturas
from app.modelos.estados import Estados
from app.esquemas.complemento import (
    ComplementoListado, ComplementoListadoConResumen, ComplementoDetalle,
    ResumenComplementos, FiltrosComplemento, DocumentoRelacionado,
    FacturaDelComplemento, DocumentoHuerfano, DiagnosticoCorreos,
    ConteoPorTipo, CorreoFallido, ReporteReproceso,
)
from app.services.factura_service import (
    reconciliar, reprocesar_fallidos, contar_fallidos_pendientes,
    MAX_INTENTOS_REPROCESO,
)

router = APIRouter()


# ─────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────

# Sin los binarios: archivo_xml y archivo_pdf pesan y no se usan en el
# listado. Se devuelven banderas calculadas en SQL.
COLUMNAS_LISTADO = (
    ComplementosPago.id,
    ComplementosPago.uuid_cp,
    ComplementosPago.folio,
    ComplementosPago.fecha_pago,
    ComplementosPago.fecha_recepcion,
    ComplementosPago.monto,
    ComplementosPago.moneda,
    ComplementosPago.tipo_cambio,
    ComplementosPago.forma_pago,
    ComplementosPago.cancelado,
    ComplementosPago.archivo_pdf.isnot(None).label("tiene_pdf"),
    ComplementosPago.archivo_xml.isnot(None).label("tiene_xml"),
)


def _existe_documento(db: Session, solo_huerfanos: bool = False):
    """EXISTS correlacionado sobre los DoctoRelacionado del CP."""
    q = db.query(CPDocumentosRelacionados.id).filter(
        CPDocumentosRelacionados.id_complemento == ComplementosPago.id
    )
    if solo_huerfanos:
        q = q.filter(CPDocumentosRelacionados.id_factura.is_(None))
    return q.exists()


def _condiciones(db: Session, filtros: FiltrosComplemento) -> list:
    condiciones = []

    if not filtros.incluir_cancelados:
        condiciones.append(ComplementosPago.cancelado == False)  # noqa: E712

    if filtros.q:
        patron = f"%{filtros.q.strip()}%"
        referido = db.query(CPDocumentosRelacionados.id).filter(
            CPDocumentosRelacionados.id_complemento == ComplementosPago.id,
            CPDocumentosRelacionados.uuid_documento.ilike(patron),
        ).exists()
        condiciones.append(or_(
            ComplementosPago.uuid_cp.ilike(patron),
            ComplementosPago.folio.ilike(patron),
            referido,
        ))

    if filtros.forma_pago:
        condiciones.append(ComplementosPago.forma_pago.ilike(f"%{filtros.forma_pago}%"))

    if filtros.fecha_desde:
        condiciones.append(ComplementosPago.fecha_pago >= filtros.fecha_desde)
    if filtros.fecha_hasta:
        condiciones.append(ComplementosPago.fecha_pago <= filtros.fecha_hasta)

    if filtros.vinculado is True:
        # Todos sus documentos pegados: tiene documentos y ninguno huérfano.
        condiciones.append(_existe_documento(db))
        condiciones.append(~_existe_documento(db, solo_huerfanos=True))
    elif filtros.vinculado is False:
        condiciones.append(_existe_documento(db, solo_huerfanos=True))

    if filtros.solo_atencion:
        condiciones.append(or_(
            ComplementosPago.archivo_pdf.is_(None),
            ComplementosPago.fecha_pago.is_(None),
            _existe_documento(db, solo_huerfanos=True),
        ))

    return condiciones


def _evaluar_atencion(tiene_pdf: bool, fecha_pago, huerfanos: int,
                      cancelado: bool) -> tuple[bool, str | None]:
    """Un CP cancelado no pide captura: ya está fuera de juego."""
    if cancelado:
        return False, None

    motivos = []
    if fecha_pago is None:
        motivos.append("sin fecha de pago en el XML")
    if huerfanos:
        motivos.append(f"{huerfanos} documento(s) sin factura vinculada")
    if not tiene_pdf:
        motivos.append("sin PDF")

    return bool(motivos), "; ".join(motivos) if motivos else None


def _documentos_por_complemento(db: Session, ids: list[int]) -> dict[int, list]:
    """
    Trae los DoctoRelacionado de varios CPs en una sola consulta.

    Una consulta por fila del listado serían 50 idas a la base por página.
    """
    if not ids:
        return {}

    filas = (
        db.query(
            CPDocumentosRelacionados,
            Facturas.id_factura,
            Facturas.folio_interno,
            Facturas.folio_fiscal,
            Facturas.cliente,
            Facturas.total,
            Estados.nombre_estado,
        )
        .outerjoin(Facturas, Facturas.id_factura == CPDocumentosRelacionados.id_factura)
        .outerjoin(Estados, Estados.id_estado == Facturas.id_estado)
        .filter(CPDocumentosRelacionados.id_complemento.in_(ids))
        .all()
    )

    agrupados: dict[int, list] = {}
    for fila in filas:
        agrupados.setdefault(fila[0].id_complemento, []).append(fila)
    return agrupados


def _arma_documento(fila) -> DocumentoRelacionado:
    doc = fila[0]
    id_factura, folio_interno, folio_fiscal, cliente, total, estado = fila[1:]

    factura = None
    if id_factura is not None:
        factura = FacturaDelComplemento(
            id_factura=id_factura,
            folio_fiscal=folio_fiscal,
            folio_interno=folio_interno,
            cliente=cliente,
            total=total,
            estado=estado,
            vinculada=True,
        )

    return DocumentoRelacionado(
        uuid_documento=doc.uuid_documento,
        num_parcialidad=doc.num_parcialidad,
        imp_pagado=doc.imp_pagado,
        imp_saldo_insoluto=doc.imp_saldo_insoluto,
        liquida=doc.imp_saldo_insoluto is not None and doc.imp_saldo_insoluto == 0,
        factura=factura,
    )


def _etiqueta_factura(fila) -> str:
    """Lo que se ve en la columna 'facturas' de la tabla."""
    folio_interno = fila[2]
    if folio_interno:
        return folio_interno
    uuid_doc = fila[0].uuid_documento or ""
    return uuid_doc[:8] or "?"


# ─────────────────────────────────────────────
# LISTADO
# ─────────────────────────────────────────────

@router.get("/", response_model=ComplementoListadoConResumen,
            tags=["Complementos de pago"])
def listar_complementos(
    filtros: FiltrosComplemento = Depends(),
    db: Session = Depends(get_db),
):
    """
    Listado paginado de complementos de pago, con filtros y totales.

    Query params (todos opcionales):
      q                   — uuid_cp, folio del CP, o UUID de la factura referida
      forma_pago          — ilike
      vinculado           — true: todos sus documentos pegados; false: con huérfanos
      solo_atencion       — true: sin PDF, sin fecha de pago, o con huérfanos
      incluir_cancelados  — false por defecto
      fecha_desde/hasta   — YYYY-MM-DD, sobre fecha_pago
      pagina, por_pagina  — 1 y 50 por defecto
    """
    condiciones = _condiciones(db, filtros)
    por_pagina = max(1, min(filtros.por_pagina, 200))

    total_registros = (
        db.query(func.count(ComplementosPago.id)).filter(*condiciones).scalar() or 0
    )
    total_paginas = ceil(total_registros / por_pagina) if total_registros else 1

    filas = (
        db.query(*COLUMNAS_LISTADO)
        .filter(*condiciones)
        # NULLS LAST: un CP sin fecha de pago es justo el que hay que revisar,
        # no el que debe encabezar la tabla.
        .order_by(desc(ComplementosPago.fecha_pago).nullslast(),
                  ComplementosPago.id.desc())
        .offset((max(1, filtros.pagina) - 1) * por_pagina)
        .limit(por_pagina)
        .all()
    )

    documentos = _documentos_por_complemento(db, [f.id for f in filas])

    resultado = []
    for fila in filas:
        docs = documentos.get(fila.id, [])
        vinculados = sum(1 for d in docs if d[1] is not None)
        huerfanos = len(docs) - vinculados

        atencion, motivo = _evaluar_atencion(
            fila.tiene_pdf, fila.fecha_pago, huerfanos, fila.cancelado
        )

        resultado.append(ComplementoListado(
            id=fila.id,
            uuid_cp=fila.uuid_cp,
            folio=fila.folio,
            fecha_pago=fila.fecha_pago,
            fecha_recepcion=fila.fecha_recepcion,
            monto=fila.monto,
            moneda=fila.moneda,
            tipo_cambio=fila.tipo_cambio,
            forma_pago=fila.forma_pago,
            cancelado=fila.cancelado,
            tiene_pdf=fila.tiene_pdf,
            tiene_xml=fila.tiene_xml,
            documentos_total=len(docs),
            documentos_vinculados=vinculados,
            documentos_huerfanos=huerfanos,
            facturas=[_etiqueta_factura(d) for d in docs],
            requiere_atencion=atencion,
            motivo_atencion=motivo,
        ))

    return ComplementoListadoConResumen(
        complementos=resultado,
        resumen=_calcular_resumen(db, condiciones),
        pagina=max(1, filtros.pagina),
        por_pagina=por_pagina,
        total_paginas=total_paginas,
    )


def _calcular_resumen(db: Session, condiciones: list) -> ResumenComplementos:
    """Totales en SQL sobre el mismo filtro del listado."""
    agregados = db.query(
        func.count(ComplementosPago.id),
        func.coalesce(func.sum(ComplementosPago.monto), 0),
        func.count(ComplementosPago.archivo_pdf),
        func.count(ComplementosPago.fecha_pago),
    ).filter(*condiciones).first()

    total, monto, con_pdf, con_fecha = agregados or (0, 0, 0, 0)

    cancelados = db.query(func.count(ComplementosPago.id)).filter(
        *condiciones, ComplementosPago.cancelado == True  # noqa: E712
    ).scalar() or 0

    con_huerfanos = db.query(func.count(ComplementosPago.id)).filter(
        *condiciones, _existe_documento(db, solo_huerfanos=True)
    ).scalar() or 0

    vinculados = db.query(func.count(ComplementosPago.id)).filter(
        *condiciones,
        _existe_documento(db),
        ~_existe_documento(db, solo_huerfanos=True),
    ).scalar() or 0

    return ResumenComplementos(
        total_complementos=total,
        total_monto=Decimal(monto or 0),
        con_pdf=con_pdf,
        sin_pdf=total - con_pdf,
        totalmente_vinculados=vinculados,
        con_huerfanos=con_huerfanos,
        cancelados=cancelados,
        sin_fecha_pago=total - con_fecha,
    )


@router.get("/resumen", response_model=ResumenComplementos,
            tags=["Complementos de pago"])
def obtener_resumen(
    filtros: FiltrosComplemento = Depends(),
    db: Session = Depends(get_db),
):
    """Los mismos totales del listado, sin traer las filas."""
    return _calcular_resumen(db, _condiciones(db, filtros))


# ─────────────────────────────────────────────
# HUÉRFANOS
# ─────────────────────────────────────────────

@router.get("/huerfanos", response_model=list[DocumentoHuerfano],
            tags=["Complementos de pago"])
def listar_huerfanos(
    limite: int = Query(200, ge=1, le=1000),
    db: Session = Depends(get_db),
):
    """
    Pagos que el CP declara contra una factura que no quedó vinculada.

    Desde el arreglo del 26/09/2026, `reconciliar()` vincula un pago con su
    factura en cualquier estado, así que solo queda una causa real de orfandad:
    la factura de ese UUID no está en la base. Casi siempre porque Monsort la
    emitió antes de que el sistema capturara correo, y la vía para traerla es
    la descarga masiva del SAT.

    El campo `motivo` distingue ese caso del transitorio, cuando la factura sí
    está y solo falta que corra la reconciliación.
    """
    filas = (
        db.query(
            CPDocumentosRelacionados,
            ComplementosPago.id,
            ComplementosPago.uuid_cp,
            ComplementosPago.folio,
            ComplementosPago.fecha_pago,
            Facturas.id_factura,
            Estados.nombre_estado,
        )
        .join(ComplementosPago,
              ComplementosPago.id == CPDocumentosRelacionados.id_complemento)
        # Por folio_fiscal, no por id_factura: justamente queremos saber si el
        # UUID que declara el CP existe en Facturas aunque no esté pegado.
        .outerjoin(Facturas,
                   Facturas.folio_fiscal == CPDocumentosRelacionados.uuid_documento)
        .outerjoin(Estados, Estados.id_estado == Facturas.id_estado)
        .filter(
            CPDocumentosRelacionados.id_factura.is_(None),
            ComplementosPago.cancelado == False,  # noqa: E712
        )
        .order_by(desc(ComplementosPago.fecha_pago).nullslast())
        .limit(limite)
        .all()
    )

    salida = []
    for doc, id_cp, uuid_cp, folio_cp, fecha_pago, id_factura, estado in filas:
        existe = id_factura is not None

        if not existe:
            motivo = (
                "La factura de ese UUID no está en la base. Se emitió antes de "
                "que el sistema capturara correo: hay que traerla con la "
                "descarga masiva del SAT"
            )
        else:
            motivo = (
                f"La factura existe (estado '{estado}'): correr "
                "/complementos/reconciliar debería vincularla"
            )

        salida.append(DocumentoHuerfano(
            id_complemento=id_cp,
            uuid_cp=uuid_cp,
            folio_cp=folio_cp,
            fecha_pago=fecha_pago,
            uuid_documento=doc.uuid_documento,
            imp_pagado=doc.imp_pagado,
            imp_saldo_insoluto=doc.imp_saldo_insoluto,
            factura_existe=existe,
            estado_factura=estado,
            motivo=motivo,
        ))

    return salida


# ─────────────────────────────────────────────
# DIAGNÓSTICO DE LA INGESTA
# ─────────────────────────────────────────────

@router.get("/diagnostico", response_model=DiagnosticoCorreos,
            tags=["Complementos de pago"])
def diagnostico(db: Session = Depends(get_db)):
    """
    Estado de la tubería de correo, sin entrar por SSH a la base.

    Esto es lo que contesta "¿se guardaron o no?": cuántos CPs hay, desde
    cuándo, cuántos documentos quedaron sin pegar, y cuántos correos se
    cayeron y siguen en la cola de reproceso.
    """
    cp_total, cp_cancelados, cp_con_pdf, cp_con_fecha, cp_ultimo = db.query(
        func.count(ComplementosPago.id),
        func.count(ComplementosPago.id).filter(ComplementosPago.cancelado == True),  # noqa: E712
        func.count(ComplementosPago.archivo_pdf),
        func.count(ComplementosPago.fecha_pago),
        func.max(ComplementosPago.fecha_recepcion),
    ).first()

    docs_total, docs_vinculados = db.query(
        func.count(CPDocumentosRelacionados.id),
        func.count(CPDocumentosRelacionados.id_factura),
    ).first()

    correos_total, correos_ultimo = db.query(
        func.count(CorreosProcesados.id),
        func.max(CorreosProcesados.fecha_procesado),
    ).first()

    por_tipo = (
        db.query(
            CorreosProcesados.tipo_correo,
            func.count(CorreosProcesados.id),
            func.max(CorreosProcesados.fecha_procesado),
        )
        .group_by(CorreosProcesados.tipo_correo)
        .order_by(func.count(CorreosProcesados.id).desc())
        .limit(20)
        .all()
    )

    fallidos_pendientes = db.query(func.count(CorreosFallidos.id)).filter(
        CorreosFallidos.resuelto == 0
    ).scalar() or 0

    fallidos_resueltos = db.query(func.count(CorreosFallidos.id)).filter(
        CorreosFallidos.resuelto == 1
    ).scalar() or 0

    ultimo_fallo = db.query(func.max(CorreosFallidos.fecha_fallo)).scalar()

    # Los primeros 120 caracteres bastan para distinguir un 403 de cuota de un
    # duplicate key; el resto de la traza es ruido para un conteo.
    # substr y no left: left() es de Postgres y esto tambien corre en SQLite
    # en las pruebas.
    prefijo = func.substr(CorreosFallidos.error, 1, 120)
    frecuentes = (
        db.query(prefijo, func.count(CorreosFallidos.id), func.max(CorreosFallidos.fecha_fallo))
        .filter(CorreosFallidos.resuelto == 0)
        .group_by(prefijo)
        .order_by(func.count(CorreosFallidos.id).desc())
        .limit(10)
        .all()
    )

    return DiagnosticoCorreos(
        complementos_guardados=cp_total or 0,
        complementos_cancelados=cp_cancelados or 0,
        complementos_sin_pdf=(cp_total or 0) - (cp_con_pdf or 0),
        complementos_sin_fecha_pago=(cp_total or 0) - (cp_con_fecha or 0),
        ultimo_complemento=cp_ultimo,
        documentos_cp_total=docs_total or 0,
        documentos_cp_vinculados=docs_vinculados or 0,
        documentos_cp_huerfanos=(docs_total or 0) - (docs_vinculados or 0),
        correos_procesados=correos_total or 0,
        ultimo_correo_procesado=correos_ultimo,
        correos_por_tipo=[
            ConteoPorTipo(tipo_correo=t, correos=c, ultimo=u) for t, c, u in por_tipo
        ],
        correos_fallidos_pendientes=fallidos_pendientes,
        correos_fallidos_reintentables=contar_fallidos_pendientes(db),
        correos_fallidos_resueltos=fallidos_resueltos,
        ultimo_fallo=ultimo_fallo,
        errores_frecuentes=[
            {"error": e, "veces": v, "ultimo": u.isoformat() if u else None}
            for e, v, u in frecuentes
        ],
    )


@router.get("/correos-fallidos", response_model=list[CorreoFallido],
            tags=["Complementos de pago"])
def listar_correos_fallidos(
    solo_pendientes: bool = True,
    limite: int = Query(100, ge=1, le=1000),
    db: Session = Depends(get_db),
):
    """Correos que la tubería no pudo procesar, con su error textual."""
    q = db.query(CorreosFallidos)
    if solo_pendientes:
        q = q.filter(CorreosFallidos.resuelto == 0)

    filas = q.order_by(CorreosFallidos.fecha_fallo.desc()).limit(limite).all()

    return [
        CorreoFallido(
            id=f.id,
            message_id=f.message_id,
            error=f.error,
            fecha_fallo=f.fecha_fallo,
            intentos=f.intentos or 0,
            resuelto=f.resuelto or 0,
            reintentable=(f.resuelto == 0 and (f.intentos or 0) < MAX_INTENTOS_REPROCESO),
        )
        for f in filas
    ]


@router.post("/reprocesar-correos", response_model=ReporteReproceso,
             tags=["Complementos de pago"])
def reprocesar_correos(
    limite: int = Query(25, ge=1, le=200),
    db: Session = Depends(get_db),
):
    """
    Reintenta los correos caídos, del más viejo al más nuevo.

    Idempotente: el dedupe es folio_fiscal / uuid_cp / hash_archivo, así que
    un correo que ya entró se detecta como existente y sólo se marca resuelto.

    Es síncrono y con pausa entre mensajes, así que un lote de 25 tarda
    alrededor de un minuto. El scheduler ya drena 40 cada 10 minutos por su
    cuenta; este endpoint es para empujar la cola a mano.
    """
    reporte = reprocesar_fallidos(db, limite=limite)

    if reporte.get("error"):
        raise HTTPException(status_code=503, detail=reporte["error"])

    return ReporteReproceso(**reporte)


@router.post("/reconciliar", tags=["Complementos de pago"])
def reconciliar_endpoint(db: Session = Depends(get_db)):
    """
    Vuelve a intentar pegar CPs con facturas y facturas con OCs.

    Sirve cuando el CP llegó antes que su factura: al entrar la factura, nadie
    reintenta el enlace hasta el siguiente ciclo de correo.
    """
    reconciliar(db)

    docs_total, docs_vinculados = db.query(
        func.count(CPDocumentosRelacionados.id),
        func.count(CPDocumentosRelacionados.id_factura),
    ).first()

    return {
        "reconciliado": True,
        "documentos_cp_total": docs_total or 0,
        "documentos_cp_vinculados": docs_vinculados or 0,
        "documentos_cp_huerfanos": (docs_total or 0) - (docs_vinculados or 0),
    }


# ─────────────────────────────────────────────
# DETALLE Y ARCHIVOS  (van al final: /{id_cp} captura todo lo anterior)
# ─────────────────────────────────────────────

@router.get("/{id_cp}", response_model=ComplementoDetalle,
            tags=["Complementos de pago"])
def obtener_complemento(id_cp: int, db: Session = Depends(get_db)):
    fila = db.query(
        *COLUMNAS_LISTADO,
        ComplementosPago.message_id,
        ComplementosPago.fecha_cancelacion,
        ComplementosPago.motivo_cancelacion,
    ).filter(ComplementosPago.id == id_cp).first()

    if not fila:
        raise HTTPException(status_code=404, detail="Complemento de pago no encontrado")

    docs = _documentos_por_complemento(db, [id_cp]).get(id_cp, [])
    huerfanos = sum(1 for d in docs if d[1] is None)

    atencion, motivo = _evaluar_atencion(
        fila.tiene_pdf, fila.fecha_pago, huerfanos, fila.cancelado
    )

    return ComplementoDetalle(
        id=fila.id,
        uuid_cp=fila.uuid_cp,
        folio=fila.folio,
        fecha_pago=fila.fecha_pago,
        fecha_recepcion=fila.fecha_recepcion,
        monto=fila.monto,
        moneda=fila.moneda,
        tipo_cambio=fila.tipo_cambio,
        forma_pago=fila.forma_pago,
        message_id=fila.message_id,
        tiene_pdf=fila.tiene_pdf,
        tiene_xml=fila.tiene_xml,
        cancelado=fila.cancelado,
        fecha_cancelacion=fila.fecha_cancelacion,
        motivo_cancelacion=fila.motivo_cancelacion,
        documentos=[_arma_documento(d) for d in docs],
        requiere_atencion=atencion,
        motivo_atencion=motivo,
    )


@router.get("/{id_cp}/pdf", tags=["Complementos de pago"])
def descargar_pdf_cp(id_cp: int, db: Session = Depends(get_db)):
    fila = db.query(
        ComplementosPago.archivo_pdf,
        ComplementosPago.folio,
        ComplementosPago.uuid_cp,
    ).filter(ComplementosPago.id == id_cp).first()

    if not fila or not fila.archivo_pdf:
        raise HTTPException(status_code=404, detail="PDF no disponible")

    nombre = fila.folio or fila.uuid_cp or str(id_cp)
    return Response(
        content=fila.archivo_pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="{nombre}.pdf"'},
    )


@router.get("/{id_cp}/xml", tags=["Complementos de pago"])
def descargar_xml_cp(id_cp: int, db: Session = Depends(get_db)):
    fila = db.query(
        ComplementosPago.archivo_xml,
        ComplementosPago.folio,
        ComplementosPago.uuid_cp,
    ).filter(ComplementosPago.id == id_cp).first()

    if not fila or not fila.archivo_xml:
        raise HTTPException(status_code=404, detail="XML no disponible")

    nombre = fila.folio or fila.uuid_cp or str(id_cp)
    return Response(
        content=fila.archivo_xml,
        media_type="application/xml",
        headers={"Content-Disposition": f'attachment; filename="{nombre}.xml"'},
    )
