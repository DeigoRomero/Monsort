from app.modelos.configuracion import Configuracion_sistema
from app.services.gmail_service import obtener_servicio_gmail, obtener_ultimo_mensaje, extraer_adjuntos, obtener_mensajes_nuevos
from app.services.usuario_service import obtener_usuario_sistema, obtener_estado_pendiente
import hashlib
import pdfplumber
import io
import re
import xml.etree.ElementTree as ET
from app.modelos.factura import Facturas, Conceptos
from app.modelos.correo_procesado import CorreosProcesados, CorreosFallidos
from app.modelos.orden_compra import OrdenesCompra
from app.modelos.complemento_pago import ComplementosPago
from app.modelos.cp_documento_relacionado import CPDocumentosRelacionados
from app.services.usuario_service import obtener_estado
from datetime import datetime


# ---------- PARSEO XML ----------

def extraer_datos_xml(contenido_xml_bytes):
    namespaces = {
        'cfdi': 'http://www.sat.gob.mx/cfd/4',
        'tfd': 'http://www.sat.gob.mx/TimbreFiscalDigital'
    }

    root = ET.fromstring(contenido_xml_bytes)

    subtotal = root.get('SubTotal')
    total = root.get('Total')
    moneda = root.get('Moneda')
    tipo_cambio_raw = root.get('TipoCambio')
    tipo_cambio = None if (moneda == 'MXN' or tipo_cambio_raw == '1') else tipo_cambio_raw
    fecha = root.get('Fecha')

    serie = root.get('Serie', '')
    folio = root.get('Folio', '')
    folio_interno = f"{serie}{folio}" if serie or folio else None

    receptor = root.find('cfdi:Receptor', namespaces)
    rfc = receptor.get('Rfc')
    cliente = receptor.get('Nombre')

    timbre = root.find('.//tfd:TimbreFiscalDigital', namespaces)
    folio_fiscal = timbre.get('UUID')

    impuestos = root.find('cfdi:Impuestos', namespaces)
    iva = impuestos.get('TotalImpuestosTrasladados') if impuestos is not None else None

    conceptos_lista = []
    numero_oc = None

    conceptos_xml = root.findall('.//cfdi:Concepto', namespaces)
    for concepto in conceptos_xml:
        descripcion = concepto.get('Descripcion', '')

        if not numero_oc:
            numero_oc = extraer_numero_oc(descripcion)

        conceptos_lista.append({
            'descripcion': descripcion,
            'cantidad': concepto.get('Cantidad'),
            'unidad': concepto.get('ClaveUnidad'),
            'precio_unitario': concepto.get('ValorUnitario'),
            'importe': concepto.get('Importe')
        })

    return {
        'folio_fiscal': folio_fiscal,
        'folio_interno': folio_interno,
        'rfc': rfc,
        'cliente': cliente,
        'fecha': fecha,
        'subtotal': subtotal,
        'iva': iva,
        'total': total,
        'moneda': moneda,
        'tipo_cambio': tipo_cambio,
        'numero_oc': numero_oc,
        'conceptos': conceptos_lista
    }


def extraer_datos_cp(xml_bytes):
    namespaces = {
        'cfdi': 'http://www.sat.gob.mx/cfd/4',
        'pago20': 'http://www.sat.gob.mx/Pagos20',
        'tfd': 'http://www.sat.gob.mx/TimbreFiscalDigital'
    }

    root = ET.fromstring(xml_bytes)

    serie = root.get('Serie', '')
    folio = root.get('Folio', '')
    folio_interno = f"{serie}{folio}" if serie or folio else None

    timbre = root.find('.//tfd:TimbreFiscalDigital', namespaces)
    uuid_cp = timbre.get('UUID')

    documentos = []
    fecha_pago = None
    moneda = None
    tipo_cambio = None
    monto = None
    forma_pago = None

    pagos = root.findall('.//pago20:Pago', namespaces)
    for pago in pagos:
        fecha_pago = pago.get('FechaPago')
        moneda = pago.get('MonedaP')
        tipo_cambio_raw = pago.get('TipoCambioP')
        tipo_cambio = None if (moneda == 'MXN' or tipo_cambio_raw == '1') else tipo_cambio_raw
        monto = pago.get('Monto')
        forma_pago = pago.get('FormaDePagoP')

        for docto in pago.findall('pago20:DoctoRelacionado', namespaces):
            documentos.append({
                'uuid_documento': docto.get('IdDocumento'),
                'num_parcialidad': docto.get('NumParcialidad'),
                'imp_pagado': docto.get('ImpPagado'),
                'imp_saldo_insoluto': docto.get('ImpSaldoInsoluto')
            })

    return {
        'uuid_cp': uuid_cp,
        'folio_interno': folio_interno,
        'fecha_pago': fecha_pago,
        'moneda': moneda,
        'tipo_cambio': tipo_cambio,
        'monto': monto,
        'forma_pago': forma_pago,
        'documentos': documentos
    }


# ---------- PDF ----------

def extraer_texto_pdf(contenido_bytes):
    with pdfplumber.open(io.BytesIO(contenido_bytes)) as pdf:
        texto_completo = ""
        for pagina in pdf.pages:
            texto_completo += pagina.extract_text() or ""
    return texto_completo


def clasificar_pdf(contenido_bytes, uuid_factura):
    texto_completo = extraer_texto_pdf(contenido_bytes)

    # Solo el encabezado: ahi va el titulo del documento.
    # La factura menciona "Orden de compra: XXXX" en los conceptos,
    # pero eso queda muy abajo y no alcanza el encabezado.
    encabezado = texto_completo[:300].lower()
    texto_lower = texto_completo.lower()

    patrones_oc = [
        r'orden de compra',
        r'purchase order',
        r'p\.o\.',
        r'\bpo\b',
    ]
    for patron in patrones_oc:
        if re.search(patron, encabezado):
            return "orden_compra"

    if 'factura' in encabezado or 'tipo cfdi' in encabezado:
        return "factura"

    # Fallback: UUID sin guiones ni espacios, porque pdfplumber lo parte en lineas
    if uuid_factura:
        uuid_limpio = uuid_factura.replace('-', '').lower()
        texto_plano = texto_lower.replace('-', '').replace(' ', '').replace('\n', '')
        if uuid_limpio in texto_plano:
            return "factura"

    return "desconocido"


def extraer_numero_oc(texto):
    if not texto:
        return None

    patrones = [
        r'PO#[_:\s]*([A-Z0-9]+)',   
        r'P\.O\.[#:\s]*([A-Z0-9]+)', 
        r'PO[:\s#\.]+([A-Z0-9]+)',
        r'PO NUMBER[:\s#]*([A-Z0-9]+)',
        r'PO N[Oo][:\s#]*([A-Z0-9]+)',  
        r'OC[:\s#\.]+([A-Z0-9]+)', 
        r'Purchase Order[:\s#]*([A-Z0-9]+)',
        r'Orden de [Cc]ompra[:\s#]*([A-Z0-9]+)',
        r'N[uú]mero Orden de Compra[:\s#]*([A-Z0-9]+)',
        r'N[uú]mero de OC[:\s#]*([A-Z0-9]+)',
    ]
    for patron in patrones:
        resultado = re.search(patron, texto, re.IGNORECASE)
        if resultado:
            return resultado.group(1).strip()
    return None


# ---------- ROUTER ----------

def detectar_tipo_correo(adjuntos: dict) -> tuple[str, bytes | None]:
    xml_bytes = None
    for nombre, contenido in adjuntos.items():
        if nombre.lower().endswith('.xml'):
            xml_bytes = contenido
            break

    if not xml_bytes:
        tiene_pdf = any(n.lower().endswith('.pdf') for n in adjuntos)
        return ("orden_compra", None) if tiene_pdf else ("desconocido", None)

    try:
        root = ET.fromstring(xml_bytes)
        tipo_comprobante = root.get('TipoDeComprobante')

        if tipo_comprobante == 'I':
            return "factura", xml_bytes
        elif tipo_comprobante == 'P':
            return "complemento_pago", xml_bytes
        return "desconocido", xml_bytes
    except ET.ParseError:
        return "desconocido", None


# ---------- CAMINOS ----------

def procesar_factura(adjuntos, xml_bytes, mensaje_id, db, usuario_sistema):
    datos_xml = extraer_datos_xml(xml_bytes)
    uuid_factura = datos_xml['folio_fiscal']

    # Dedupe: la misma factura no se guarda dos veces
    existente = db.query(Facturas).filter(
        Facturas.folio_fiscal == uuid_factura
    ).first()
    if existente:
        return

    pdf_factura_bytes = None
    pdf_oc_bytes = None

    for nombre, contenido in adjuntos.items():
        if not nombre.lower().endswith('.pdf'):
            continue
        tipo = clasificar_pdf(contenido, uuid_factura)
        if tipo == "factura":
            pdf_factura_bytes = contenido
        elif tipo == "orden_compra":
            pdf_oc_bytes = contenido

    # Estado inicial segun si detectamos el numero de OC
    if datos_xml['numero_oc']:
        estado = obtener_estado(db, "Pendiente de factura")
    else:
        estado = obtener_estado(db, "Requiere captura manual")

    nueva_factura = Facturas(
        folio_fiscal=uuid_factura,
        folio_interno=datos_xml['folio_interno'],
        rfc=datos_xml['rfc'],
        cliente=datos_xml['cliente'],
        fecha=datetime.fromisoformat(datos_xml['fecha']).date(),
        subtotal=float(datos_xml['subtotal']) if datos_xml['subtotal'] else None,
        iva=float(datos_xml['iva']) if datos_xml['iva'] else None,
        total=float(datos_xml['total']) if datos_xml['total'] else None,
        tipo_cambio=datos_xml['tipo_cambio'],
        numero_oc=datos_xml['numero_oc'],
        numero_oc_detectado=datos_xml['numero_oc'],
        pdf_factura=pdf_factura_bytes,
        orden_compra_archivo=pdf_oc_bytes,
        xml_factura=xml_bytes,
        message_id=mensaje_id,
        id_usuario=usuario_sistema.id_usuario,
        id_estado=estado.id_estado
    )
    db.add(nueva_factura)

    for concepto in datos_xml['conceptos']:
        nuevo_concepto = Conceptos(
            descripcion=concepto['descripcion'],
            cantidad=float(concepto['cantidad']) if concepto['cantidad'] else None,
            unidad=concepto['unidad'],
            precio_unitario=float(concepto['precio_unitario']) if concepto['precio_unitario'] else None,
            importe=float(concepto['importe']) if concepto['importe'] else None,
            factura=nueva_factura
        )
        db.add(nuevo_concepto)

    db.commit()


def procesar_complemento_pago(adjuntos, xml_bytes, mensaje_id, db):
    datos_cp = extraer_datos_cp(xml_bytes)

    # Dedupe por UUID del CP
    existente = db.query(ComplementosPago).filter(
        ComplementosPago.uuid_cp == datos_cp['uuid_cp']
    ).first()
    if existente:
        return

    pdf_bytes = None
    for nombre, contenido in adjuntos.items():
        if nombre.lower().endswith('.pdf'):
            pdf_bytes = contenido
            break

    nuevo_complemento = ComplementosPago(
        uuid_cp=datos_cp['uuid_cp'],
        folio=datos_cp['folio_interno'],
        fecha_pago=datetime.fromisoformat(datos_cp['fecha_pago']) if datos_cp['fecha_pago'] else None,
        moneda=datos_cp['moneda'],
        tipo_cambio=datos_cp['tipo_cambio'],
        monto=float(datos_cp['monto']) if datos_cp['monto'] else None,
        forma_pago=datos_cp['forma_pago'],
        archivo_xml=xml_bytes,
        archivo_pdf=pdf_bytes,
        hash_archivo=hashlib.sha256(pdf_bytes).hexdigest() if pdf_bytes else None,
        message_id=mensaje_id
    )
    db.add(nuevo_complemento)

    for doc in datos_cp['documentos']:
        nuevo_doc = CPDocumentosRelacionados(
            uuid_documento=doc['uuid_documento'],
            num_parcialidad=int(doc['num_parcialidad']) if doc['num_parcialidad'] else None,
            imp_pagado=float(doc['imp_pagado']) if doc['imp_pagado'] else None,
            imp_saldo_insoluto=float(doc['imp_saldo_insoluto']) if doc['imp_saldo_insoluto'] else None,
            id_factura=None,   # reconciliar() lo resuelve
            complemento=nuevo_complemento
        )
        db.add(nuevo_doc)

    db.commit()


def procesar_orden_compra(adjuntos, asunto, mensaje_id, db):
    pdf_bytes = None
    nombre_pdf = None

    for nombre, contenido in adjuntos.items():
        if nombre.lower().endswith('.pdf'):
            pdf_bytes = contenido
            nombre_pdf = nombre
            break

    if not pdf_bytes:
        return

    hash_archivo = hashlib.sha256(pdf_bytes).hexdigest()

    # Si este archivo exacto ya existe, no duplicar
    existente = db.query(OrdenesCompra).filter(
        OrdenesCompra.hash_archivo == hash_archivo
    ).first()
    if existente:
        return

    numero_detectado = extraer_numero_oc(asunto)
    if not numero_detectado and nombre_pdf:
        numero_detectado = extraer_numero_oc(nombre_pdf)
    if not numero_detectado:
        numero_detectado = extraer_numero_oc(extraer_texto_pdf(pdf_bytes)[:600])

    nueva_oc = OrdenesCompra(
        numero_oc=numero_detectado,
        numero_oc_detectado=numero_detectado,
        archivo=pdf_bytes,
        nombre_archivo=nombre_pdf,
        hash_archivo=hash_archivo,
        message_id=mensaje_id
    )
    db.add(nueva_oc)
    db.commit() 


def contar_facturas_pendientes(db):
    estado_pendiente = obtener_estado_pendiente(db)
    return db.query(Facturas).filter(Facturas.id_estado == estado_pendiente.id_estado).count()

def reconciliar(db):
    """
    Enlaza OCs con facturas y CPs con facturas. Idempotente:
    se puede correr cuantas veces sea, solo toca lo que falta.
    Se llama desde el scheduler y desde el endpoint de correccion manual.
    """
    estado_captura = obtener_estado(db, "Requiere captura manual")
    estado_pendiente_cp = obtener_estado(db, "Pendiente de CP")
    estado_revision = obtener_estado(db, "Pendiente de revisión")

    if not all([estado_captura, estado_pendiente_cp, estado_revision]):
        raise ValueError("Faltan estados en la BD: verifica los nombres en la tabla Estados")

    # --- 1. Enlazar documentos de CP con facturas ---
    docs_sueltos = db.query(CPDocumentosRelacionados).filter(
        CPDocumentosRelacionados.id_factura.is_(None)
    ).all()

    for doc in docs_sueltos:
        factura = db.query(Facturas).filter(
            Facturas.folio_fiscal == doc.uuid_documento
        ).first()
        if factura:
            doc.id_factura = factura.id_factura

    db.flush()

    # --- 2. Enlazar facturas con su OC ---
    facturas_sin_oc = db.query(Facturas).filter(
        Facturas.id_orden_compra.is_(None),
        Facturas.numero_oc.isnot(None)
    ).all()

    for factura in facturas_sin_oc:
        ocs = db.query(OrdenesCompra).filter(
            OrdenesCompra.numero_oc == factura.numero_oc
        ).all()

        if len(ocs) == 1:
            factura.id_orden_compra = ocs[0].id
        # 0 o >1: se deja sin enlazar, el estado lo resuelve el paso 3

    db.flush()

    # --- 3. Recalcular estado y fecha_liquidacion de cada factura ---
    for factura in db.query(Facturas).all():
        # Buscar el CP que liquido esta factura (saldo insoluto en cero)
        doc_liquidacion = db.query(CPDocumentosRelacionados).filter(
            CPDocumentosRelacionados.id_factura == factura.id_factura,
            CPDocumentosRelacionados.imp_saldo_insoluto == 0
        ).first()

        if doc_liquidacion:
            cp = db.query(ComplementosPago).filter(
                ComplementosPago.id == doc_liquidacion.id_complemento
            ).first()
            if cp and cp.fecha_pago:
                factura.fecha_liquidacion = cp.fecha_pago.date()
            factura.id_estado = estado_revision.id_estado

        elif not factura.numero_oc:
            factura.id_estado = estado_captura.id_estado

        elif factura.id_orden_compra:
            factura.id_estado = estado_pendiente_cp.id_estado

        else:
            # Tiene numero de OC pero no se pudo enlazar (no existe o ambiguo)
            factura.id_estado = estado_captura.id_estado

    db.commit()

def procesar_correos_nuevos(db):
    servicio = obtener_servicio_gmail()
    ids_mensajes, nuevo_history_id = obtener_mensajes_nuevos(db)
    ids_mensajes = list(dict.fromkeys(ids_mensajes))
    usuario_sistema = obtener_usuario_sistema(db)

    for mensaje_id in ids_mensajes:
        if db.query(CorreosProcesados).filter(CorreosProcesados.message_id == mensaje_id).first():
            continue
        try:
            adjuntos, asunto = extraer_adjuntos(servicio, mensaje_id)
            tipo, xml_bytes = detectar_tipo_correo(adjuntos)

            if tipo == "orden_compra":
                procesar_orden_compra(adjuntos, asunto, mensaje_id, db)
            elif tipo == "factura":
                procesar_factura(adjuntos, xml_bytes, mensaje_id, db, usuario_sistema)
            elif tipo == "complemento_pago":
                procesar_complemento_pago(adjuntos, xml_bytes, mensaje_id, db)

            db.add(CorreosProcesados(
                message_id=mensaje_id,
                tipo_correo=tipo,
                fecha_procesado=datetime.now()
            ))
            db.commit()
        except Exception as e:
            db.rollback()
            db.add(CorreosFallidos(
                message_id=mensaje_id,
                error=str(e),
                fecha_fallo=datetime.now(),
                resuelto=0
            ))
            db.commit()

    # Reconciliar despues de procesar todo el lote
    try:
        reconciliar(db)
    except Exception as e:
        db.rollback()
        print(f"Error en reconciliacion: {e}")

    if nuevo_history_id:
        id_guardado = db.query(Configuracion_sistema).filter(
            Configuracion_sistema.clave == "gmail_history_id"
        ).first()
        id_guardado.valor = nuevo_history_id
        db.commit()