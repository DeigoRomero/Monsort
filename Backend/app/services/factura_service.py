from app.modelos.configuracion import Configuracion_sistema
from app.services.gmail_service import (
    obtener_servicio_gmail, obtener_ultimo_mensaje, extraer_adjuntos,
    obtener_mensajes_nuevos, es_error_permanente,
)
from app.services.usuario_service import obtener_usuario_sistema, obtener_estado_pendiente
from google.auth.exceptions import RefreshError
from app.core.config import settings
from sqlalchemy import or_
from decimal import Decimal, InvalidOperation
import hashlib
import pdfplumber
import io
import re
import time
import zipfile
import logging
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


logger = logging.getLogger(__name__)
ESTADOS_TERMINALES = ("Cancelada", "Revisada", "Histórico")

# Ancho de CorreosProcesados.tipo_correo despues de la migracion c4f1a9b27d30.
LARGO_TIPO_CORREO = 120
# El error se guarda completo en Postgres (String sin limite), pero una
# traza de 40 KB en una tabla que se consulta a mano no le sirve a nadie.
LARGO_ERROR = 2000

# Reproceso de CorreosFallidos
MAX_INTENTOS_REPROCESO = 5
PAUSA_ENTRE_REPROCESOS = 0.5   # segundos, para no volver a reventar la cuota


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
    emisor = root.find('cfdi:Emisor', namespaces)
    rfc_emisor = emisor.get('Rfc', '').upper().strip() if emisor is not None else ''
    nombre_emisor = emisor.get('Nombre', '').strip() if emisor is not None else ''
    receptor = root.find('cfdi:Receptor', namespaces)
    rfc = receptor.get('Rfc', '').upper().strip()
    nombre_receptor = receptor.get('Nombre', '').strip()    

    timbre = root.find('.//tfd:TimbreFiscalDigital', namespaces)
    folio_fiscal = normalizar_uuid(timbre.get('UUID'))

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
        'rfc_emisor': rfc_emisor,
        'nombre_emisor': nombre_emisor,
        'fecha': fecha,
        'subtotal': subtotal,
        'iva': iva,
        'total': total,
        'moneda': moneda,
        'tipo_cambio': tipo_cambio,
        'numero_oc': numero_oc,
        'conceptos': conceptos_lista
    }


def normalizar_uuid(valor):
    """
    Deja todo folio fiscal en MAYUSCULAS y sin espacios.

    El SAT especifica el UUID en mayusculas, pero no todos los emisores lo
    respetan: hay PACs que escriben el IdDocumento de un DoctoRelacionado en
    minusculas. Como reconciliar() compara con == y Postgres distingue
    mayusculas, el mismo documento con distinta caja no se encuentra nunca.
    Al 26/09/2026 eso dejaba 23 de 46 pagos sin pegar a su factura.

    Se normaliza al ESCRIBIR, no al consultar: asi todas las comparaciones que
    ya existen en el proyecto siguen sirviendo sin tocarlas una por una.
    """
    if valor is None:
        return None
    limpio = str(valor).strip().upper()
    return limpio or None


def _tag_local(elemento) -> str:
    """'{http://...Pagos20}Pago' -> 'Pago'."""
    tag = elemento.tag
    if not isinstance(tag, str):
        return ""
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def _buscar_por_tag(raiz, nombre: str) -> list:
    """
    Busca por nombre local, ignorando el namespace.

    Deliberado: los CP viven en Pagos20 (CFDI 4.0) pero tambien llegan en
    Pagos10 (CFDI 3.3), y el prefijo real del documento puede ser cualquiera.
    Amarrarse a la URI del namespace es lo que hacia que un CP de la version
    vieja saliera con fecha_pago=None y, con fecha_pago NOT NULL, se llevara
    el correo completo al rollback. Los nombres de atributo son identicos en
    ambas versiones, asi que con el nombre local alcanza.
    """
    return [e for e in raiz.iter() if _tag_local(e) == nombre]


def _a_decimal(valor):
    if valor in (None, ""):
        return None
    try:
        return Decimal(str(valor))
    except (InvalidOperation, ValueError):
        return None


def extraer_datos_cp(xml_bytes):
    root = ET.fromstring(xml_bytes)

    serie = root.get('Serie', '')
    folio = root.get('Folio', '')
    folio_interno = f"{serie}{folio}" if serie or folio else None

    timbres = _buscar_por_tag(root, 'TimbreFiscalDigital')
    uuid_cp = normalizar_uuid(timbres[0].get('UUID')) if timbres else None

    documentos = []
    fecha_pago = None
    moneda = None
    tipo_cambio = None
    forma_pago = None
    monto_total = None

    pagos = _buscar_por_tag(root, 'Pago')

    if len(pagos) > 1:
        # Un CP con varios pagos es legal. El modelo guarda un solo monto, asi
        # que se suma el total y la fecha/forma se toman del primero; los
        # DoctoRelacionado de todos los pagos si se conservan completos.
        logger.warning(
            "CP %s trae %d nodos Pago: monto = suma de los %d, "
            "fecha y forma de pago tomadas del primero",
            uuid_cp, len(pagos), len(pagos)
        )

    for indice, pago in enumerate(pagos):
        if indice == 0:
            fecha_pago = pago.get('FechaPago')
            moneda = (pago.get('MonedaP') or 'MXN').upper()
            # Invariante del proyecto: MXN siempre guarda '1', asi se evita
            # ramificar por None en toda la logica de conversion.
            tipo_cambio = '1' if moneda == 'MXN' else pago.get('TipoCambioP')
            forma_pago = pago.get('FormaDePagoP')

        parcial = _a_decimal(pago.get('Monto'))
        if parcial is not None:
            monto_total = parcial if monto_total is None else monto_total + parcial

        for docto in _buscar_por_tag(pago, 'DoctoRelacionado'):
            documentos.append({
                # normalizado: hay emisores que lo escriben en minusculas
                'uuid_documento': normalizar_uuid(docto.get('IdDocumento')),
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
        'monto': monto_total,
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

def _expandir_zips(adjuntos: dict) -> dict:
    """
    Sustituye cada .zip por los XML y PDF que trae dentro.

    Muchos proveedores mandan el CFDI zipeado ("CP576.zip" con el XML y el
    PDF). El filtro MIME de gmail_service acepta application/octet-stream, que
    es como suele viajar un zip, asi que el archivo si se descargaba pero
    nadie lo abria: el CP quedaba invisible.

    Las claves quedan como "CP576.zip:CP576.xml" para que en el log se vea de
    donde salio cada pieza.
    """
    planos: dict[str, bytes] = {}

    for nombre, contenido in adjuntos.items():
        if not nombre.lower().endswith(".zip"):
            planos[nombre] = contenido
            continue

        try:
            with zipfile.ZipFile(io.BytesIO(contenido)) as archivo_zip:
                internos = 0
                for interno in archivo_zip.namelist():
                    base = interno.rsplit("/", 1)[-1]
                    if not base.lower().endswith((".xml", ".pdf")):
                        continue

                    clave = f"{nombre}:{base}"
                    sufijo = 2
                    while clave in planos:
                        clave = f"{nombre}:{base}#{sufijo}"
                        sufijo += 1

                    planos[clave] = archivo_zip.read(interno)
                    internos += 1

                logger.info("ZIP %s: %d documento(s) extraido(s)", nombre, internos)

        except (zipfile.BadZipFile, RuntimeError, OSError) as e:
            # RuntimeError = zip con contrasena. No se puede hacer nada con el,
            # pero tampoco debe tumbar el resto del correo.
            logger.warning("ZIP ilegible %s: %s", nombre, e)

    return planos


def clasificar_adjuntos(adjuntos: dict) -> dict:
    """
    Devuelve:
        {
          'facturas':     [(nombre, xml_bytes), ...],   # TipoDeComprobante = I
          'complementos': [(nombre, xml_bytes), ...],   # TipoDeComprobante = P
          'pdfs':         {nombre: contenido, ...},
          'otros':        [nombre, ...]
        }

    La clasificacion es por TipoDeComprobante dentro del XML, no por el nombre
    del archivo: que el CP se llame "CP576.xml" es una costumbre del emisor,
    no una regla.
    """
    resultado = {"facturas": [], "complementos": [], "pdfs": {}, "otros": []}

    for nombre, contenido in _expandir_zips(adjuntos).items():
        nombre_lower = nombre.lower()

        if nombre_lower.endswith(".xml"):
            try:
                root = ET.fromstring(contenido)
            except ET.ParseError as e:
                logger.info("XML ilegible %s: %s", nombre, e)
                resultado["otros"].append(nombre)
                continue

            tipo = root.get("TipoDeComprobante")
            if tipo == "I":
                resultado["facturas"].append((nombre, contenido))
            elif tipo == "P":
                resultado["complementos"].append((nombre, contenido))
            else:
                # Aqui si existe 'tipo'. Antes este log vivia en la rama de
                # "ni XML ni PDF", donde la variable no estaba asignada, y
                # levantaba UnboundLocalError con el correo entero adentro.
                logger.info(
                    "XML con TipoDeComprobante=%r no procesado: %s", tipo, nombre
                )
                resultado["otros"].append(nombre)

        elif nombre_lower.endswith(".pdf"):
            resultado["pdfs"][nombre] = contenido

        else:
            logger.info("Adjunto no procesable, se ignora: %s", nombre)
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

    if datos_xml['rfc_emisor'] != settings.RFC_EMPRESA:
        logger.warning(
            "Factura %s: RFC emisor '%s' no coincide con RFC empresa '%s'. Se ignora.",
            uuid_factura,
            datos_xml['rfc_emisor'],
            settings.RFC_EMPRESA
        )
        return False

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

    cliente_obj = resolver_cliente(db, datos_xml['rfc'], datos_xml['nombre_receptor'])

    nueva_factura = Facturas(
        folio_fiscal=uuid_factura,
        folio_interno=datos_xml['folio_interno'],
        rfc=datos_xml['rfc'],
        cliente=datos_xml['nombre_receptor'],
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
        # Se congela el plazo vigente al momento de emitir: si el cliente
        # renegocia despues, las facturas viejas conservan el suyo.
        # Queda None si el cliente aun no tiene plazo capturado.
        dias_plazo_pago_aplicado=(
            cliente_obj.dias_plazo_pago if cliente_obj else None
        ),
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
    # PENDIENTE: los clientes nuevos nacen sin dias_plazo_pago, asi que su
    # primera factura queda sin fecha limite hasta que alguien lo capture
    # en el dashboard. Decidir si conviene un valor por defecto.
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

    if not datos_cp['uuid_cp']:
        # Sin UUID no hay llave natural: guardarlo significaria no poder
        # deduplicarlo nunca.
        raise ValueError(
            f"CP sin TimbreFiscalDigital/UUID en el correo {mensaje_id}. "
            "XML sin timbrar o fuera de norma."
        )

    existente = db.query(ComplementosPago).filter(
        ComplementosPago.uuid_cp == datos_cp['uuid_cp']
    ).first()
    if existente:
        return False

    if not datos_cp['fecha_pago']:
        # fecha_pago es nullable desde c4f1a9b27d30. Se guarda igual y queda
        # visible en /complementos para captura manual: mejor un CP incompleto
        # en la base que un correo entero perdido.
        logger.warning(
            "CP %s sin nodo Pago legible (correo %s). Se guarda sin fecha de "
            "pago para revision manual.",
            datos_cp['uuid_cp'], mensaje_id
        )

    pdf_bytes = buscar_pdf_por_uuid(indice_pdfs, datos_cp['uuid_cp'])
    if pdf_bytes is None:
        pdf_bytes = reclamar_pdf_restante(indice_pdfs)

    nuevo_complemento = ComplementosPago(
        uuid_cp=datos_cp['uuid_cp'],
        folio=datos_cp['folio_interno'],
        fecha_pago=datetime.fromisoformat(datos_cp['fecha_pago']) if datos_cp['fecha_pago'] else None,
        moneda=datos_cp['moneda'],
        tipo_cambio=datos_cp['tipo_cambio'],
        monto=datos_cp['monto'],
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

    Cada documento va en su propio SAVEPOINT. Antes todo el correo era una
    sola transacción "o entran todas o ninguna", y en la práctica eso
    significaba que un CP con un dato raro se llevaba la factura buena que
    venía en el mismo correo. Ahora lo que se puede guardar se guarda y lo
    que falla queda contado en resumen['errores'].

    Devuelve {'facturas': n, 'complementos': n, 'ordenes': n, 'errores': n}
    """
    clasificados = clasificar_adjuntos(adjuntos)
    indice_pdfs = indexar_pdfs(clasificados["pdfs"])

    resumen = {"facturas": 0, "complementos": 0, "ordenes": 0, "errores": 0}

    def _aislado(etiqueta: str, funcion) -> bool:
        """Ejecuta funcion() en un savepoint. False si reventó."""
        try:
            with db.begin_nested():
                return bool(funcion())
        except Exception:
            resumen["errores"] += 1
            logger.exception(
                "Correo %s: fallo al procesar %s. Se continúa con el resto "
                "del correo.", mensaje_id, etiqueta
            )
            return False

    for nombre, xml_bytes in clasificados["facturas"]:
        if _aislado(
            f"factura {nombre}",
            lambda xb=xml_bytes: procesar_factura(
                xb, mensaje_id, db, usuario_sistema, indice_pdfs
            ),
        ):
            resumen["facturas"] += 1

    for nombre, xml_bytes in clasificados["complementos"]:
        if _aislado(
            f"complemento {nombre}",
            lambda xb=xml_bytes: procesar_complemento_pago(
                xb, mensaje_id, db, indice_pdfs
            ),
        ):
            resumen["complementos"] += 1

    for item in reclamar_ocs_sueltas(indice_pdfs):
        if _aislado(
            f"orden de compra {item['nombre']}",
            lambda it=item: procesar_orden_compra_suelta(
                it, asunto, mensaje_id, db
            ),
        ):
            resumen["ordenes"] += 1

    # Correo con PDFs pero sin XML ni encabezado de OC reconocible:
    # se tratan como OC de todos modos (comportamiento del código anterior).
    # 'errores' queda fuera de la condición a propósito: un correo cuyo único
    # documento falló no debe caer en el camino de las OCs sueltas.
    if not (resumen["facturas"] or resumen["complementos"] or resumen["ordenes"]):
        for item in indice_pdfs:
            if item["asignado"] or not item["es_oc"]:
                continue
            if _aislado(
                f"orden de compra suelta {item['nombre']}",
                lambda it=item: procesar_orden_compra_suelta(
                    it, asunto, mensaje_id, db
                ),
            ):
                resumen["ordenes"] += 1
            else:
                # PDF que parece OC pero ya existe en la base.
                # Se marca asignado para que no se reintente.
                item["asignado"] = True

        # Si quedaron PDFs sin asignar y sin patrón de OC, se ignoran.
        # Es preferible no capturar a llenar OrdenesCompra de basura.
        sin_clasificar = [i for i in indice_pdfs if not i["asignado"]]
        if sin_clasificar:
            logger.info(
                "Correo %s: %d PDF(s) sin clasificar ignorados: %s",
                mensaje_id,
                len(sin_clasificar),
                [i["nombre"] for i in sin_clasificar],
            )

    return resumen


def describir_resumen(resumen: dict) -> str:
    """
    Texto para CorreosProcesados.tipo_correo, ej: 'facturas:3+ordenes:1'.

    Se recorta al ancho de la columna: este valor se inserta DESPUÉS de
    guardar los documentos, así que un desborde aquí revertía el correo
    completo. La migración c4f1a9b27d30 subió la columna a 120, el recorte
    es el cinturón de seguridad.
    """
    partes = [f"{k}:{v}" for k, v in resumen.items() if v]
    texto = "+".join(partes) if partes else "desconocido"
    return texto[:LARGO_TIPO_CORREO]


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

    Distingue tres cosas que antes iban detras del mismo filtro de estados
    terminales, y esa mezcla escondia pagos:

      1. VINCULAR un documento de CP con su factura: se hace SIEMPRE, en
         cualquier estado. Que una factura este Cancelada o venga del
         historico del Excel no cambia el hecho de que ese pago la
         referencia, y registrarlo es informacion que el cliente necesita.
         Antes se excluian los estados terminales, y como 718 de 755
         facturas estan en 'Historico', casi cualquier pago contra el
         historico se perdia en el aire.
      2. fecha_liquidacion: tambien se escribe en cualquier estado. Es un
         hecho ("esta factura se pago el dia X"), no una decision de flujo.
      3. id_estado: SOLO se recalcula en facturas no terminales. Aqui si hay
         que respetar la frontera, porque 'Cancelada', 'Revisada' e
         'Historico' son decisiones tomadas que reconciliar() no debe
         deshacer.

    Los CPs cancelados se siguen excluyendo de todo.
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
        # Sin filtro de estado: vincular es registrar un hecho, no mover el
        # flujo. El estado se respeta mas abajo, en el paso 3.
        factura = db.query(Facturas).filter(
            Facturas.folio_fiscal == doc.uuid_documento
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

    # --- 3. fecha_liquidacion (todas) y estado (solo las no terminales) ---
    #
    # Se recorren TODAS las facturas que tengan un CP vinculado, mas las
    # activas. Antes solo se miraban las activas, asi que una factura del
    # historico podia quedar con su pago vinculado y sin fecha_liquidacion.
    ids_con_cp = [
        fila[0] for fila in db.query(CPDocumentosRelacionados.id_factura)
        .filter(CPDocumentosRelacionados.id_factura.isnot(None))
        .distinct()
        .all()
    ]

    condiciones = [Facturas.id_estado.notin_(ids_terminales)]
    if ids_con_cp:
        condiciones.append(Facturas.id_factura.in_(ids_con_cp))

    facturas = db.query(Facturas).filter(or_(*condiciones)).all()

    for factura in facturas:
        es_terminal = factura.id_estado in ids_terminales

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
                # Se escribe incluso en terminales: es un hecho fiscal.
                factura.fecha_liquidacion = cp.fecha_pago.date()

        if es_terminal:
            # 'Cancelada', 'Revisada' e 'Historico' son decisiones tomadas.
            continue

        if doc_liquidacion:
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

def _registrar_fallo(db, mensaje_id: str, error: Exception) -> None:
    """
    Anota el fallo en CorreosFallidos sin duplicar filas.

    Si ya hay una fila pendiente para ese message_id, se actualiza y se sube
    el contador. Sin esto, el reproceso automático generaría una fila nueva
    cada vez que reintenta el mismo correo.
    """
    texto = f"{type(error).__name__}: {error}"[:LARGO_ERROR]

    fila = db.query(CorreosFallidos).filter(
        CorreosFallidos.message_id == mensaje_id,
        CorreosFallidos.resuelto == 0,
    ).first()

    if fila:
        fila.error = texto
        fila.fecha_fallo = datetime.now()
        fila.intentos = (fila.intentos or 0) + 1
        if es_error_permanente(error):
            # 404: el mensaje ya no existe en Gmail. Reintentar es quemar
            # cuota a cambio de nada.
            fila.intentos = max(fila.intentos, MAX_INTENTOS_REPROCESO)
    else:
        db.add(CorreosFallidos(
            message_id=mensaje_id,
            error=texto,
            fecha_fallo=datetime.now(),
            resuelto=0,
            intentos=MAX_INTENTOS_REPROCESO if es_error_permanente(error) else 1,
        ))


def _procesar_un_correo(db, servicio, mensaje_id: str, usuario_sistema) -> dict:
    """
    Baja un correo, guarda sus documentos y lo marca como procesado.

    Commit al final: las excepciones suben al llamador, que se encarga del
    rollback y de CorreosFallidos.
    """
    adjuntos, asunto = extraer_adjuntos(servicio, mensaje_id)

    # Procesa TODOS los documentos del correo, no solo el primero
    resumen = procesar_correo(adjuntos, asunto, mensaje_id, db, usuario_sistema)

    db.add(CorreosProcesados(
        message_id=mensaje_id,
        tipo_correo=describir_resumen(resumen),
        fecha_procesado=datetime.now()
    ))
    db.commit()
    return resumen


def procesar_correos_nuevos(db):
    try:
        servicio = obtener_servicio_gmail(db)
            # Se pasa el servicio ya construido: antes se hacia un segundo build()
            # + refresh de token en cada ciclo.
        ids_mensajes, nuevo_history_id = obtener_mensajes_nuevos(db, servicio)
    except RefreshError as e:
        logger.error(
            "Credenciales de Gmail inválidas (%s)."
            "Reatuoriza en /auth/gmail/iniciar", e
        )
        return
    except RuntimeError as e:
        logger.error("Gmail no configurado: %s", e)
        return
    
    ids_mensajes = list(dict.fromkeys(ids_mensajes))
    usuario_sistema = obtener_usuario_sistema(db)

    procesados = 0
    fallidos = 0

    for mensaje_id in ids_mensajes:
        if db.query(CorreosProcesados).filter(CorreosProcesados.message_id == mensaje_id).first():
            continue
        try:
            _procesar_un_correo(db, servicio, mensaje_id, usuario_sistema)
            procesados += 1
        except Exception as e:
            db.rollback()
            _registrar_fallo(db, mensaje_id, e)
            db.commit()
            fallidos += 1
            logger.warning("Correo %s no procesado: %s", mensaje_id, e)

    if procesados or fallidos:
        logger.info(
            "Ciclo de correos: %d procesado(s), %d fallido(s) de %d en el historial",
            procesados, fallidos, len(ids_mensajes)
        )

    try:
        reconciliar(db)
    except Exception:
        db.rollback()
        logger.exception("Error en reconciliacion")

    if nuevo_history_id:
        id_guardado = db.query(Configuracion_sistema).filter(
            Configuracion_sistema.clave == "gmail_history_id"
        ).first()
        # La fila deberia existir siempre que nuevo_history_id venga poblado,
        # pero la guarda evita un AttributeError si esa invariante cambia.
        if id_guardado:
            id_guardado.valor = nuevo_history_id
            db.commit()


# ---------- REPROCESO DE CORREOS FALLIDOS ----------

def contar_fallidos_pendientes(db) -> int:
    return db.query(CorreosFallidos).filter(
        CorreosFallidos.resuelto == 0,
        CorreosFallidos.intentos < MAX_INTENTOS_REPROCESO,
    ).count()


def reprocesar_fallidos(db, limite: int = 25) -> dict:
    """
    Vuelve a intentar los correos que quedaron en CorreosFallidos.

    Por qué esto es la única vía de recuperación: el historial de Gmail sólo
    guarda alrededor de una semana, así que volver a leer por historyId no
    sirve para lo que se perdió en septiembre. Lo que sí se guardó de cada
    correo caído es su message_id, y con ese ID el mensaje se puede pedir
    directo mientras siga en el buzón.

    Es seguro repetirlo: el dedupe real es folio_fiscal / uuid_cp /
    hash_archivo, así que un correo que ya entró no se duplica — se detecta
    como existente y se marca resuelto.

    Los correos con error permanente (404: mensaje borrado del buzón) nacen
    con intentos = MAX_INTENTOS_REPROCESO, así que no consumen turno.
    """
    try:
        servicio = obtener_servicio_gmail(db)
    except RefreshError as e:
        logger.error(
            "Reproceso cancelado, credenciales de Gmail inválidas (%s). "
            "Reautoriza en /auth/gmail/iniciar", e
        )
        return {"error": "credenciales_invalidas"}
    except RuntimeError as e:
        logger.error("Reproceso cancelado, Gmail no configurado: %s", e)
        return {"error": "gmail_no_configurado"}

    usuario_sistema = obtener_usuario_sistema(db)

    pendientes = (
        db.query(CorreosFallidos)
        .filter(
            CorreosFallidos.resuelto == 0,
            CorreosFallidos.intentos < MAX_INTENTOS_REPROCESO,
        )
        .order_by(CorreosFallidos.fecha_fallo.asc())
        .limit(limite)
        .all()
    )

    reporte = {
        "intentados": 0,
        "recuperados": 0,
        "ya_estaban": 0,
        "siguen_fallando": 0,
        "documentos": {"facturas": 0, "complementos": 0, "ordenes": 0},
        "detalle": [],
    }

    for fila in pendientes:
        fila_id = fila.id
        mensaje_id = fila.message_id
        reporte["intentados"] += 1

        ya_procesado = db.query(CorreosProcesados).filter(
            CorreosProcesados.message_id == mensaje_id
        ).first()
        if ya_procesado:
            fila.resuelto = 1
            db.commit()
            reporte["ya_estaban"] += 1
            continue

        try:
            resumen = _procesar_un_correo(db, servicio, mensaje_id, usuario_sistema)

            fila_actual = db.query(CorreosFallidos).filter(
                CorreosFallidos.id == fila_id
            ).first()
            if fila_actual:
                fila_actual.resuelto = 1
                fila_actual.intentos = (fila_actual.intentos or 0) + 1
                db.commit()

            reporte["recuperados"] += 1
            for clave in reporte["documentos"]:
                reporte["documentos"][clave] += resumen.get(clave, 0)
            reporte["detalle"].append({
                "message_id": mensaje_id,
                "resultado": describir_resumen(resumen),
            })

        except Exception as e:
            db.rollback()
            _registrar_fallo(db, mensaje_id, e)
            db.commit()
            reporte["siguen_fallando"] += 1
            reporte["detalle"].append({
                "message_id": mensaje_id,
                "resultado": f"{type(e).__name__}: {str(e)[:180]}",
            })

        # Los picos del 11 y 18 de septiembre fueron exactamente esto sin
        # pausa: miles de llamadas seguidas y 403 por cuota.
        time.sleep(PAUSA_ENTRE_REPROCESOS)

    if reporte["recuperados"]:
        try:
            reconciliar(db)
        except Exception:
            db.rollback()
            logger.exception("Error reconciliando tras el reproceso")

    reporte["pendientes_restantes"] = contar_fallidos_pendientes(db)

    logger.info(
        "Reproceso: %d intentado(s), %d recuperado(s), %d ya estaban, "
        "%d siguen fallando, %d pendiente(s) en cola",
        reporte["intentados"], reporte["recuperados"], reporte["ya_estaban"],
        reporte["siguen_fallando"], reporte["pendientes_restantes"],
    )
    return reporte