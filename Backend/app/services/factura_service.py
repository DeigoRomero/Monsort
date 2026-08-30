from app.modelos.configuracion import Configuracion_sistema
from app.services.gmail_service import obtener_servicio_gmail, obtener_ultimo_mensaje, extraer_adjuntos, obtener_mensajes_nuevos
from app.services.usuario_service import obtener_usuario_sistema, obtener_estado_pendiente
import hashlib
import pdfplumber
import io
import re
import xml.etree.ElementTree as ET
from app.modelos.factura import Facturas, HistorialVerificacion
from app.modelos.conceptos import Conceptos
from app.modelos.correo_procesado import CorreosProcesados, CorreosFallidos
from app.modelos.orden_compra import OrdenesCompra
from app.modelos.complemento_pago import ComplementosPago
from app.modelos.cp_documento_relacionado import CPDocumentosRelacionados
from app.modelos.cliente import Cliente
from app.modelos.estados import Estados
from app.services.usuario_service import obtener_estado
from datetime import datetime, date


ESTADOS_TERMINALES = ("Cancelada", "Revisada", "Histórico")


def _ids_estados_terminales(db) -> list[int]:
    filas = db.query(Estados.id_estado).filter(
        Estados.nombre_estado.in_(ESTADOS_TERMINALES)
    ).all()
    return [f[0] for f in filas]


def _obtener_id_estado_cancelada(db) -> int:
    estado = obtener_estado(db, "Cancelada")
    if not estado:
        raise ValueError("Estado 'Cancelada' no encontrado en la BD")
    return estado.id_estado


# ---------- PARSEO XML ----------

def extraer_datos_xml(contenido_xml_bytes):
    namespaces = {
        'cfdi': 'http://www.sat.gob.mx/cfd/4',
        'tfd': 'http://www.sat.gob.mx/TimbreFiscalDigital'
    }

    root = ET.fromstring(contenido_xml_bytes)

    subtotal = root.get('SubTotal')
    total = root.get('Total')
    moneda = root.get('Moneda', 'MXN').upper()
    tipo_cambio_raw = root.get('TipoCambio')

    if moneda == 'MXN':
        tipo_cambio = '1'
    else:
        tipo_cambio = tipo_cambio_raw
    fecha = root.get('Fecha')

    serie = root.get('Serie', '')
    folio = root.get('Folio', '')
    folio_interno = f"{serie}{folio}" if serie or folio else None

    receptor = root.find('cfdi:Receptor', namespaces)
    rfc = receptor.get('Rfc', '').upper().strip()
    nombre_receptor = receptor.get('Nombre', '').strip()    

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
        'nombre_receptor': nombre_receptor,
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
        moneda = pago.get('MonedaP', 'MXN').upper()
        tipo_cambio_raw = pago.get('TipoCambioP')

        if moneda == 'MXN':
            tipo_cambio = '1'
        else:
            tipo_cambio = tipo_cambio_raw
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


PATRONES_OC_ENCABEZADO = [
    r'orden de compra',
    r'purchase order',
    r'p\.o\.',
    r'\bpo\b',
]


def clasificar_pdf(contenido_bytes, uuid_factura):
    """
    OBSOLETA: el flujo nuevo usa indexar_pdfs() + buscar_pdf_por_uuid().
    Se conserva por si algún script la importa.
    """
    texto_completo = extraer_texto_pdf(contenido_bytes)
    encabezado = texto_completo[:300].lower()
    texto_lower = texto_completo.lower()

    for patron in PATRONES_OC_ENCABEZADO:
        if re.search(patron, encabezado):
            return "orden_compra"

    if 'factura' in encabezado or 'tipo cfdi' in encabezado:
        return "factura"

    if uuid_factura:
        uuid_limpio = uuid_factura.replace('-', '').lower()
        texto_plano = texto_lower.replace('-', '').replace(' ', '').replace('\n', '')
        if uuid_limpio in texto_plano:
            return "factura"

    return "desconocido"


def extraer_numero_oc(texto):
    if not texto:
        return None

    patrones_con_keyword = [
        r'\bPO#[_:\s]*([A-Z0-9]+)',
        r'\bP\.O\.[#:\s]*([A-Z0-9]+)',
        r'\bPO[:\s#\.]+([A-Z0-9]+)',
        r'\bPO NUMBER[:\s#]*([A-Z0-9]+)',
        r'\bPO N[Oo][:\s#]*([A-Z0-9]+)',
        r'\bOC[:\s#\.]+([A-Z0-9]+)',
        r'\bPurchase Order[:\s#]*([A-Z0-9]+)',
        r'\bOrden de [Cc]ompra[:\s#]*([A-Z0-9]+)',
        r'\bN[uú]mero Orden de Compra[:\s#]*([A-Z0-9]+)',
        r'\bN[uú]mero de OC[:\s#]*([A-Z0-9]+)',
    ]
    for patron in patrones_con_keyword:
        resultado = re.search(patron, texto, re.IGNORECASE)
        if resultado:
            valor = resultado.group(1).strip()
            if any(c.isdigit() for c in valor):
                return valor

    candidatos = re.findall(r'\b[A-Z]{0,3}\d{4,}\b', texto.upper())
    if candidatos:
        return candidatos[0]

    return None


# ═══════════════════════════════════════════════════════════════════════════
# CLASIFICACIÓN DE ADJUNTOS
#
# Reemplaza a detectar_tipo_correo(): no se detiene en el primer XML.
# Un correo puede traer varias facturas, varios CPs, o una mezcla.
# ═══════════════════════════════════════════════════════════════════════════

def clasificar_adjuntos(adjuntos: dict) -> dict:
    """
    Devuelve:
        {
          'facturas':     [(nombre, xml_bytes), ...],   # TipoDeComprobante = I
          'complementos': [(nombre, xml_bytes), ...],   # TipoDeComprobante = P
          'pdfs':         {nombre: contenido, ...},
          'otros':        [nombre, ...]
        }
    """
    resultado = {"facturas": [], "complementos": [], "pdfs": {}, "otros": []}

    for nombre, contenido in adjuntos.items():
        nombre_lower = nombre.lower()

        if nombre_lower.endswith(".xml"):
            try:
                root = ET.fromstring(contenido)
                tipo = root.get("TipoDeComprobante")
                if tipo == "I":
                    resultado["facturas"].append((nombre, contenido))
                elif tipo == "P":
                    resultado["complementos"].append((nombre, contenido))
                else:
                    resultado["otros"].append(nombre)
            except ET.ParseError:
                resultado["otros"].append(nombre)

        elif nombre_lower.endswith(".pdf"):
            resultado["pdfs"][nombre] = contenido

        else:
            resultado["otros"].append(nombre)

    return resultado


# ═══════════════════════════════════════════════════════════════════════════
# ÍNDICE DE PDFs
#
# El texto se extrae UNA sola vez por PDF (antes era una vez por cada par
# PDF × factura). 'asignado' evita que dos facturas reclamen el mismo archivo.
# ═══════════════════════════════════════════════════════════════════════════

def indexar_pdfs(pdfs: dict) -> list[dict]:
    indice = []
    for nombre, contenido in pdfs.items():
        try:
            texto = extraer_texto_pdf(contenido)
        except Exception:
            texto = ""

        encabezado = texto[:300].lower()

        indice.append({
            "nombre": nombre,
            "contenido": contenido,
            "texto": texto,
            # UUID sin guiones ni espacios: pdfplumber parte el UUID en líneas
            "texto_plano": texto.lower().replace("-", "").replace(" ", "").replace("\n", ""),
            "encabezado": encabezado,
            "es_oc": any(re.search(p, encabezado) for p in PATRONES_OC_ENCABEZADO),
            "asignado": False,
        })
    return indice


def buscar_pdf_por_uuid(indice: list[dict], uuid_documento: str):
    """
    Localiza el PDF que corresponde a ESTE documento por su UUID.
    Con varias facturas en un correo, el UUID es el único criterio confiable:
    el encabezado dice "Factura" en todas.
    """
    if not uuid_documento:
        return None

    uuid_limpio = uuid_documento.replace("-", "").lower()

    for item in indice:
        if item["asignado"] or item["es_oc"]:
            continue
        if uuid_limpio in item["texto_plano"]:
            item["asignado"] = True
            return item["contenido"]

    return None


def reclamar_pdf_restante(indice: list[dict]):
    """
    Fallback para cuando el match por UUID falla (PDF escaneado sin capa
    de texto). Solo asigna si queda EXACTAMENTE un PDF sin reclamar:
    ante ambigüedad prefiere no asignar nada a asignar mal.
    """
    disponibles = [i for i in indice if not i["asignado"] and not i["es_oc"]]
    if len(disponibles) == 1:
        disponibles[0]["asignado"] = True
        return disponibles[0]["contenido"]
    return None


def buscar_pdf_oc(indice: list[dict], numero_oc: str | None):
    """Localiza el PDF de orden de compra que corresponde a ESTA factura."""
    if not numero_oc:
        return None

    objetivo = numero_oc.upper()
    for item in indice:
        if item["asignado"] or not item["es_oc"]:
            continue
        if objetivo in item["texto"].upper():
            item["asignado"] = True
            return item["contenido"]

    return None


def reclamar_ocs_sueltas(indice: list[dict]) -> list[dict]:
    """PDFs de OC que ninguna factura reclamó: se guardan como OCs propias."""
    return [i for i in indice if i["es_oc"] and not i["asignado"]]


# ---------- CAMINOS ----------

def procesar_factura(xml_bytes, mensaje_id, db, usuario_sistema, indice_pdfs) -> bool:
    """Devuelve True si insertó una factura nueva, False si ya existía."""
    datos_xml = extraer_datos_xml(xml_bytes)
    uuid_factura = datos_xml['folio_fiscal']

    existente = db.query(Facturas).filter(
        Facturas.folio_fiscal == uuid_factura
    ).first()
    if existente:
        return False

    # PDF de la factura: por UUID (criterio fuerte), con fallback si el
    # PDF no tiene capa de texto y es el único disponible
    pdf_factura_bytes = buscar_pdf_por_uuid(indice_pdfs, uuid_factura)
    if pdf_factura_bytes is None:
        pdf_factura_bytes = reclamar_pdf_restante(indice_pdfs)

    # PDF de OC que venga en el mismo correo y coincida con su numero_oc
    pdf_oc_bytes = buscar_pdf_oc(indice_pdfs, datos_xml['numero_oc'])

    if datos_xml['numero_oc']:
        estado = obtener_estado(db, "Pendiente de factura")
    else:
        estado = obtener_estado(db, "Requiere captura manual")

    cliente_obj = resolver_cliente(db, datos_xml['rfc'], datos_xml['cliente'])

    nueva_factura = Facturas(
        folio_fiscal=uuid_factura,
        folio_interno=datos_xml['folio_interno'],
        rfc=datos_xml['rfc'],
        cliente=datos_xml['cliente'],
        fecha=datetime.fromisoformat(datos_xml['fecha']).date(),
        subtotal=float(datos_xml['subtotal']) if datos_xml['subtotal'] else None,
        iva=float(datos_xml['iva']) if datos_xml['iva'] else None,
        total=float(datos_xml['total']) if datos_xml['total'] else None,
        moneda=datos_xml['moneda'],
        tipo_cambio=datos_xml['tipo_cambio'],
        numero_oc=datos_xml['numero_oc'],
        numero_oc_detectado=datos_xml['numero_oc'],
        pdf_factura=pdf_factura_bytes,
        orden_compra_archivo=pdf_oc_bytes,
        xml_factura=xml_bytes,
        message_id=mensaje_id,
        id_usuario=usuario_sistema.id_usuario,
        id_estado=estado.id_estado,
        id_cliente=cliente_obj.id if cliente_obj else None,
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

    # flush, no commit: las N facturas del correo entran en una sola
    # transacción. O entran todas, o ninguna.
    db.flush()
    return True

def resolver_cliente(db, rfc: str, nombre: str) -> Cliente:

    existente = db.query(Cliente).filter(
        Cliente.rfc == rfc,
    ).first()
    if existente:
        return existente
    nuevo_cliente = Cliente(
        rfc=rfc,
        nombre=nombre,
    )
    db.add(nuevo_cliente)
    db.flush()
    return nuevo_cliente


def procesar_complemento_pago(xml_bytes, mensaje_id, db, indice_pdfs) -> bool:
    """Devuelve True si insertó un CP nuevo, False si ya existía."""
    datos_cp = extraer_datos_cp(xml_bytes)

    existente = db.query(ComplementosPago).filter(
        ComplementosPago.uuid_cp == datos_cp['uuid_cp']
    ).first()
    if existente:
        return False

    pdf_bytes = buscar_pdf_por_uuid(indice_pdfs, datos_cp['uuid_cp'])
    if pdf_bytes is None:
        pdf_bytes = reclamar_pdf_restante(indice_pdfs)

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
            id_factura=None,
            complemento=nuevo_complemento
        )
        db.add(nuevo_doc)

    db.flush()
    return True


def procesar_orden_compra_suelta(item_indice, asunto, mensaje_id, db) -> bool:
    """
    Guarda un PDF de OC que ninguna factura reclamó.
    Recibe un item del índice: el texto ya está extraído, no se relee el PDF.
    """
    pdf_bytes = item_indice["contenido"]
    nombre_pdf = item_indice["nombre"]
    hash_archivo = hashlib.sha256(pdf_bytes).hexdigest()

    existente = db.query(OrdenesCompra).filter(
        OrdenesCompra.hash_archivo == hash_archivo
    ).first()
    if existente:
        item_indice["asignado"] = True
        return False

    numero_detectado = extraer_numero_oc(asunto)
    if not numero_detectado:
        numero_detectado = extraer_numero_oc(nombre_pdf)
    if not numero_detectado:
        numero_detectado = extraer_numero_oc(item_indice["texto"][:600])

    nueva_oc = OrdenesCompra(
        numero_oc=numero_detectado,
        numero_oc_detectado=numero_detectado,
        archivo=pdf_bytes,
        nombre_archivo=nombre_pdf,
        hash_archivo=hash_archivo,
        message_id=mensaje_id
    )
    db.add(nueva_oc)
    item_indice["asignado"] = True
    db.flush()
    return True


# ═══════════════════════════════════════════════════════════════════════════
# DISPATCHER DE CORREO
# ═══════════════════════════════════════════════════════════════════════════

def procesar_correo(adjuntos, asunto, mensaje_id, db, usuario_sistema) -> dict:
    """
    Procesa TODOS los documentos de un correo, sin importar cuántos sean
    ni si vienen mezclados.

    Orden deliberado:
      1. Facturas    — reclaman su PDF por UUID y su OC por numero_oc
      2. CPs         — reclaman su PDF por UUID
      3. OCs sueltas — lo que quedó sin reclamar

    Devuelve {'facturas': n, 'complementos': n, 'ordenes': n}
    """
    clasificados = clasificar_adjuntos(adjuntos)
    indice_pdfs = indexar_pdfs(clasificados["pdfs"])

    resumen = {"facturas": 0, "complementos": 0, "ordenes": 0}

    for _, xml_bytes in clasificados["facturas"]:
        if procesar_factura(xml_bytes, mensaje_id, db, usuario_sistema, indice_pdfs):
            resumen["facturas"] += 1

    for _, xml_bytes in clasificados["complementos"]:
        if procesar_complemento_pago(xml_bytes, mensaje_id, db, indice_pdfs):
            resumen["complementos"] += 1

    for item in reclamar_ocs_sueltas(indice_pdfs):
        if procesar_orden_compra_suelta(item, asunto, mensaje_id, db):
            resumen["ordenes"] += 1

    # Correo con PDFs pero sin XML ni encabezado de OC reconocible:
    # se tratan como OC de todos modos (comportamiento del código anterior)
    if not any(resumen.values()):
        for item in indice_pdfs:
            if not item["asignado"]:
                if procesar_orden_compra_suelta(item, asunto, mensaje_id, db):
                    resumen["ordenes"] += 1

    return resumen


def describir_resumen(resumen: dict) -> str:
    """Texto para CorreosProcesados.tipo_correo, ej: 'facturas:3+ordenes:1'."""
    partes = [f"{k}:{v}" for k, v in resumen.items() if v]
    return "+".join(partes) if partes else "desconocido"


# ---------- CONSULTAS AUXILIARES ----------

def contar_facturas_pendientes(db):
    estado_pendiente = obtener_estado_pendiente(db)
    return db.query(Facturas).filter(Facturas.id_estado == estado_pendiente.id_estado).count()


def _tiene_cp_activo(db, id_factura: int) -> bool:
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


# ---------- RECONCILIACIÓN ----------

def reconciliar(db):
    """
    Enlaza OCs con facturas y CPs con facturas. Idempotente.
    NO toca facturas en estado terminal (Cancelada, Revisada), ni CPs
    cancelados.
    """
    estado_captura = obtener_estado(db, "Requiere captura manual")
    estado_pendiente_cp = obtener_estado(db, "Pendiente de CP")
    estado_revision = obtener_estado(db, "Pendiente de revisión")

    if not all([estado_captura, estado_pendiente_cp, estado_revision]):
        raise ValueError("Faltan estados en la BD: verifica los nombres en la tabla Estados")

    ids_terminales = _ids_estados_terminales(db)

    # --- 1. Enlazar documentos de CP con facturas (excluir CPs cancelados) ---
    docs_sueltos = (
        db.query(CPDocumentosRelacionados)
        .join(ComplementosPago, CPDocumentosRelacionados.id_complemento == ComplementosPago.id)
        .filter(
            CPDocumentosRelacionados.id_factura.is_(None),
            ComplementosPago.cancelado == False          # noqa: E712
        )
        .all()
    )

    for doc in docs_sueltos:
        factura = db.query(Facturas).filter(
            Facturas.folio_fiscal == doc.uuid_documento,
            Facturas.id_estado.notin_(ids_terminales)
        ).first()
        if factura:
            doc.id_factura = factura.id_factura

    db.flush()

    # --- 2. Enlazar facturas con su OC ---
    facturas_sin_oc = db.query(Facturas).filter(
        Facturas.id_orden_compra.is_(None),
        Facturas.numero_oc.isnot(None),
        Facturas.id_estado.notin_(ids_terminales)
    ).all()

    for factura in facturas_sin_oc:
        ocs = db.query(OrdenesCompra).filter(
            OrdenesCompra.numero_oc == factura.numero_oc
        ).all()

        if len(ocs) == 1:
            factura.id_orden_compra = ocs[0].id

    db.flush()

    # --- 3. Recalcular estado y fecha_liquidacion ---
    facturas_activas = db.query(Facturas).filter(
        Facturas.id_estado.notin_(ids_terminales)
    ).all()

    for factura in facturas_activas:
        doc_liquidacion = (
            db.query(CPDocumentosRelacionados)
            .join(ComplementosPago, CPDocumentosRelacionados.id_complemento == ComplementosPago.id)
            .filter(
                CPDocumentosRelacionados.id_factura == factura.id_factura,
                CPDocumentosRelacionados.imp_saldo_insoluto == 0,
                ComplementosPago.cancelado == False      # noqa: E712
            )
            .first()
        )

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
            factura.id_estado = estado_captura.id_estado

    db.commit()


# ---------- REVISIÓN MANUAL ----------

def marcar_revisada(db, id_factura: int, id_usuario: int) -> tuple[bool, str]:
    """
    Marca la factura como Revisada si tiene OC vinculada Y CP activo.
    Estado terminal: reconciliar() ya no la recalcula.

    Devuelve (aplicada, motivo).
    """
    factura = db.query(Facturas).filter(Facturas.id_factura == id_factura).first()
    if not factura:
        return False, "Factura no encontrada"

    estado_revisada = obtener_estado(db, "Revisada")
    if not estado_revisada:
        return False, "Estado 'Revisada' no existe en la BD"

    if factura.id_estado == estado_revisada.id_estado:
        return False, "La factura ya está marcada como revisada"

    id_cancelada = _obtener_id_estado_cancelada(db)
    if factura.id_estado == id_cancelada:
        return False, "No se puede revisar una factura cancelada"

    estado_historico = obtener_estado(db, "Histórico")
    if estado_historico and factura.id_estado == estado_historico.id_estado:
        return False, "No se puede revisar una factura del histórico migrado"

    tiene_oc = factura.id_orden_compra is not None
    tiene_cp = _tiene_cp_activo(db, id_factura)

    if not tiene_oc or not tiene_cp:
        faltantes = []
        if not tiene_oc:
            faltantes.append("orden de compra")
        if not tiene_cp:
            faltantes.append("complemento de pago")
        return False, f"Falta vincular: {', '.join(faltantes)}"

    factura.id_estado = estado_revisada.id_estado

    db.add(HistorialVerificacion(
        id_factura=id_factura,
        id_estado=estado_revisada.id_estado,
        id_usuario=id_usuario,
        fecha_verificacion=date.today(),
        resultado_verificacion="Ciclo completo verificado manualmente desde el dashboard",
        origen="manual"
    ))

    db.commit()
    return True, "Factura marcada como revisada"


def revertir_revisada(db, id_factura: int, id_usuario: int) -> tuple[bool, str]:
    """
    Saca a la factura del estado Revisada para que reconciliar() la retome.
    Se usa cuando se cancela un CP que la respaldaba.
    """
    factura = db.query(Facturas).filter(Facturas.id_factura == id_factura).first()
    if not factura:
        return False, "Factura no encontrada"

    estado_revisada = obtener_estado(db, "Revisada")
    if not estado_revisada or factura.id_estado != estado_revisada.id_estado:
        return False, "La factura no está en estado Revisada"

    estado_pendiente = obtener_estado(db, "Pendiente de revisión")
    factura.id_estado = estado_pendiente.id_estado

    db.add(HistorialVerificacion(
        id_factura=id_factura,
        id_estado=estado_pendiente.id_estado,
        id_usuario=id_usuario,
        fecha_verificacion=date.today(),
        resultado_verificacion="Revisión revertida: cambió la documentación de respaldo",
        origen="manual"
    ))

    db.commit()
    return True, "Revisión revertida"


# ---------- CANCELACIONES ----------

def cancelar_factura(db, id_factura: int, motivo: str, id_usuario: int) -> Facturas:
    """
    Cancela una factura administrativamente:
    - Cambia su estado a Cancelada (terminal)
    - Registra el evento en HistorialVerificacion
    """
    factura = db.query(Facturas).filter(Facturas.id_factura == id_factura).first()
    if not factura:
        raise ValueError("Factura no encontrada")

    id_estado_cancelada = _obtener_id_estado_cancelada(db)

    if factura.id_estado == id_estado_cancelada:
        raise ValueError("La factura ya está cancelada")

    factura.id_estado = id_estado_cancelada

    db.add(HistorialVerificacion(
        id_factura=id_factura,
        id_estado=id_estado_cancelada,
        id_usuario=id_usuario,
        fecha_verificacion=date.today(),
        resultado_verificacion=motivo or "Cancelación administrativa",
        origen="manual"
    ))

    db.commit()
    return factura


def cancelar_cp(db, id_cp: int, motivo: str, id_usuario: int) -> ComplementosPago:
    """
    Cancela un CP administrativamente:
    - Marca cancelado=True con auditoría
    - Revierte fecha_liquidacion en las facturas que liquidó
    - Saca del estado Revisada a las facturas que ese CP respaldaba,
      o reconciliar() nunca las recalcularía (estado terminal)
    """
    cp = db.query(ComplementosPago).filter(ComplementosPago.id == id_cp).first()
    if not cp:
        raise ValueError("Complemento de pago no encontrado")

    if cp.cancelado:
        raise ValueError("El complemento de pago ya está cancelado")

    cp.cancelado = True
    cp.fecha_cancelacion = date.today()
    cp.motivo_cancelacion = motivo or "Cancelación administrativa"
    cp.cancelado_por = id_usuario

    docs = db.query(CPDocumentosRelacionados).filter(
        CPDocumentosRelacionados.id_complemento == id_cp,
        CPDocumentosRelacionados.id_factura.isnot(None)
    ).all()

    estado_revisada = obtener_estado(db, "Revisada")
    ids_a_revertir = []

    for doc in docs:
        factura = db.query(Facturas).filter(
            Facturas.id_factura == doc.id_factura
        ).first()
        if not factura:
            continue

        factura.fecha_liquidacion = None

        if estado_revisada and factura.id_estado == estado_revisada.id_estado:
            ids_a_revertir.append(factura.id_factura)

    db.commit()

    # Sacar del estado terminal para que reconciliar() las retome
    for id_f in ids_a_revertir:
        revertir_revisada(db, id_f, id_usuario)

    reconciliar(db)

    return cp


# ---------- SCHEDULER ----------

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

            # Procesa TODOS los documentos del correo, no solo el primero
            resumen = procesar_correo(adjuntos, asunto, mensaje_id, db, usuario_sistema)

            db.add(CorreosProcesados(
                message_id=mensaje_id,
                tipo_correo=describir_resumen(resumen),
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