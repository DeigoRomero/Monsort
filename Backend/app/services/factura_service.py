from app.services.gmail_service import obtener_servicio_gmail, obtener_ultimo_mensaje, extraer_adjuntos, obtener_mensajes_nuevos
from app.services.usuario_service import obtener_usuario_sistema, obtener_estado_pendiente
import pdfplumber
import io
import re
import xml.etree.ElementTree as ET
from app.modelos.factura import Facturas, Conceptos
from datetime import datetime

def extraer_datos_xml(contenido_xml_bytes):
    namespaces = {
        'cfdi': 'http://www.sat.gob.mx/cfd/4',
        'tfd': 'http://www.sat.gob.mx/TimbreFiscalDigital'
    }
    
    root = ET.fromstring(contenido_xml_bytes)
    
    # Datos generales de la factura
    subtotal = root.get('SubTotal')
    total = root.get('Total')
    
    receptor = root.find('cfdi:Receptor', namespaces)
    rfc = receptor.get('Rfc')
    cliente = receptor.get('Nombre')
    fecha = root.get('Fecha')
    
    timbre = root.find('.//tfd:TimbreFiscalDigital', namespaces)
    folio_fiscal = timbre.get('UUID')
    
    # Buscar el IVA 
    impuestos = root.find('cfdi:Impuestos', namespaces)
    iva = impuestos.get('TotalImpuestosTrasladados') if impuestos is not None else None
    
    # Lista de conceptos, en vez de una suma
    conceptos_lista = []
    conceptos_xml = root.findall('.//cfdi:Concepto', namespaces)
    for concepto in conceptos_xml:
        conceptos_lista.append({
            'descripcion': concepto.get('Descripcion'),
            'cantidad': concepto.get('Cantidad'),
            'unidad': concepto.get('ClaveUnidad'),
            'precio_unitario': concepto.get('ValorUnitario'),
            'importe': concepto.get('Importe')
        })
    
    return {
        'folio_fiscal': folio_fiscal,
        'rfc': rfc,
        'cliente': cliente,
        'fecha': fecha,
        'subtotal': subtotal,
        'iva': iva,
        'total': total,
        'conceptos': conceptos_lista
    }

def clasificar_pdf(contenido_bytes, uuid_factura):
    with pdfplumber.open(io.BytesIO(contenido_bytes)) as pdf:
        texto_completo = ""
        for pagina in pdf.pages:
            texto_completo += pagina.extract_text() or ""

    if uuid_factura and uuid_factura in texto_completo:
        return "factura"
    elif "orden de compra" in texto_completo.lower() or "oc" in texto_completo.lower():
        return "orden_compra"
    else:
        return "desconocido" 

def extraer_texto_pdf(contenido_bytes):
    with pdfplumber.open(io.BytesIO(contenido_bytes)) as pdf:
        texto_completo = ""
        for pagina in pdf.pages:
            texto_completo += pagina.extract_text() or ""
    return texto_completo

def extraer_folio_interno(texto_pdf):
    patron = r"Folio:\s*(\S+)"
    resultado = re.search(patron, texto_pdf)
    if resultado:
        return resultado.group(1)
    return None

def extraer_orden_compra(nombre_archivo_oc, texto_pdf_factura):
    # 1. Intentar sacarlo del texto del PDF de factura (más confiable)
    patron_texto = r"Orden de compra:\s*(\S+)"
    resultado = re.search(patron_texto, texto_pdf_factura, re.IGNORECASE)
    if resultado:
        return resultado.group(1)
    
    # 2. Si no se encontró en el texto, intentar sacarlo del nombre del archivo de la OC
    patron_nombre = r"OC[.\s]?(\d+)"
    resultado = re.search(patron_nombre, nombre_archivo_oc, re.IGNORECASE)
    if resultado:
        return resultado.group(1)
    
    # 3. Si tampoco, no se encontró en ningún lado
    return None

def procesar_correos_nuevos(db):
    servicio = obtener_servicio_gmail()
    mensajes_nuevos = obtener_mensajes_nuevos(db)
    usuario_sistema = obtener_usuario_sistema(db)
    estado_pendiente = obtener_estado_pendiente(db)

    for mensaje_id in mensajes_nuevos:
        adjuntos = extraer_adjuntos(servicio, mensaje_id)

        # 1. Buscar el XML entre los adjuntos
        xml_bytes = None
        for nombre, contenido in adjuntos.items():
            if nombre.endswith('.xml'):
                xml_bytes = contenido
                break

        # 2. Si no hay XML, es spam o irrelevante -> ignorar este correo
        if not xml_bytes:
            continue

        # 3. Parsear el XML
        datos_factura = extraer_datos_xml(xml_bytes)
        fecha_str=datos_factura['fecha']

        # 4. Clasificar los PDFs (factura vs orden de compra)
        pdf_factura_bytes = None
        pdf_oc_bytes = None
        nombre_pdf_oc = None
        for nombre, contenido in adjuntos.items():
            if nombre.endswith('.pdf'):
                tipo = clasificar_pdf(contenido, datos_factura['folio_fiscal'])
                if tipo == "factura":
                    pdf_factura_bytes = contenido
                elif tipo == "orden_compra":
                    pdf_oc_bytes = contenido
                    nombre_pdf_oc = nombre

        # 5. Extraer folio interno y orden de compra usando el texto del PDF de factura
        texto_pdf_factura = extraer_texto_pdf(pdf_factura_bytes) if pdf_factura_bytes else ""
        folio_interno = extraer_folio_interno(texto_pdf_factura)
        orden_compra_numero = extraer_orden_compra(nombre_pdf_oc or "", texto_pdf_factura)

        # 6. TU TAREA: crear el objeto Facturas y guardarlo
        Factura = Facturas(
            rfc=datos_factura['rfc'],
            folio_fiscal=datos_factura['folio_fiscal'],
            cliente=datos_factura['cliente'],
            fecha=datetime.fromisoformat(fecha_str).date(),
            folio_interno=folio_interno,
            subtotal=float(datos_factura['subtotal']),
            iva=float(datos_factura['iva']) if datos_factura['iva'] else None,
            total=float(datos_factura['total']),
            orden_compra=orden_compra_numero,
            pdf_factura=pdf_factura_bytes,
            xml_factura=xml_bytes,
            orden_compra_archivo=pdf_oc_bytes,
            id_usuario=usuario_sistema.id_usuario,
            id_estado=estado_pendiente.id_estado
        )

        db.add(Factura)
        db.commit()
        db.refresh(Factura)

        # 7. TU TAREA: por cada concepto en datos_factura['conceptos'], crear un objeto Conceptos ligado a la factura    

        for concepto in datos_factura['conceptos']:
            nuevo_concepto = Conceptos(
                id_factura=Factura.id_factura,
                descripcion=concepto['descripcion'],
                cantidad=float(concepto['cantidad']),
                unidad=concepto['unidad'],
                precio_unitario=float(concepto['precio_unitario']),
                importe=float(concepto['importe'])
            )
            db.add(nuevo_concepto)
        db.commit()