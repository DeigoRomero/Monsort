"""
Prueba del detector de OC contra OCs reales.

Corpus: los PDFs de ordenes_compra del respaldo de produccion (26/09/2026)
mas los tres casos que reporto el cliente el 29/09/2026. La verdad es el
numero que el usuario dejo capturado, corregido donde el PDF dice otra cosa.

Uso:
    python prueba_detector_oc.py <carpeta_con_pdfs>

La carpeta debe tener <id>.pdf y verdad.json ([{"id", "nombre", "numero"}],
numero null = no es OC). Sin carpeta corre solo los casos de texto.

El corpus del 30/09/2026 (43 OCs de produccion + 2 del cliente) da 41/41
numeros exactos y 4/4 no-OCs rechazadas; el detector anterior acertaba 27.
Para armarlo: exportar ordenes_compra.archivo a <id>.pdf. NO se sube a git:
son documentos de clientes.
"""
import json
import sys
from pathlib import Path

import pdfplumber

sys.path.insert(0, str(Path(__file__).parent))
from app.services.detector_oc import (   # noqa: E402
    detectar_numero_oc, es_documento_oc, extraer_numero_oc, clave_oc,
    cuerpo_es_orden_de_compra, html_a_texto,
)

fallos = 0


def verificar(nombre, obtenido, esperado):
    global fallos
    ok = obtenido == esperado
    if not ok:
        fallos += 1
    print(f"  {'OK ' if ok else 'MAL'} {nombre:<45} obtenido={obtenido!r:<22} esperado={esperado!r}")


def casos_de_texto():
    print("== Conceptos de CFDI (extraer_numero_oc)")
    casos = [
        ("Reemplazo de llantas. Orden de compra: 63641271", "63641271"),
        ("MISC-Servicio de suministro de pipa de agua de 10000 litros. #PO-VSM-14851", "PO-VSM-14851"),
        ("BOLSAS_DE_HIELO. ORDEN DE COMPRA: BOS-507-91753", "BOS-507-91753"),
        ("ORDEN DE COMPRA: C-10003766965", "C-10003766965"),
        ("serie: 199241 ORDEN DE COMPRA: 40083-00", "40083-00"),
        ("Sistema de enfriamiento para agua de procesos. PO:63600785", "63600785"),
        ("EXTRACCION PARA PAQUETE DUCTO COT.4934 ORDEN DE COMPRA: 729958", "729958"),
        ("Botas C- COMPOSITE TOE. ORDEN DE COMPRA: NHY13E3438", "NHY13E3438"),
        ("Servicio de pipa de agua de 10000 litros.", None),
        ("Mantenimiento Septiembre 2026", None),
        ("Servicio general. #CL-0308", "CL-0308"),
        ("Material varios OC: 0642", "0642"),
    ]
    for texto, esperado in casos:
        verificar(texto[:45], extraer_numero_oc(texto), esperado)

    print("== Llave de comparacion")
    for a, b in [("#PO-VSM-12062", "PO-VSM-12062"), ("BOS-507-91753.", "BOS-507-91753"),
                 ("40083-00", "40083"), (": NHY2542473", "NHY2542473"), ("PO26004288", "26004288")]:
        verificar(f"clave({a}) == clave({b})", clave_oc(a) == clave_oc(b), True)

    print("== OC en el cuerpo del correo (Regal Rexnord / SciQuest)")
    cuerpo_html = """<table><tr><td>Purchase Order</td></tr>
      <tr><td>Purchase Order Date</td><td>PO/Reference No.</td><td>Revision No.</td></tr>
      <tr><td>29 Sep 2026</td><td>D1009036</td><td>0</td></tr></table>
      <p>Supplier Name MONSORT SIN FRONTERAS SA DE CV</p>
      <p>Requested Delivery Date 25/09/2026</p><p>Total 1,414.00 USD</p>"""
    texto = html_a_texto(cuerpo_html)
    r = cuerpo_es_orden_de_compra("Regal Rexnord Procurement, PO#: D1009036", texto)
    verificar("SciQuest PO#: D1009036", r.numero if r else None, "D1009036")
    r = cuerpo_es_orden_de_compra("RE: Regal Rexnord Procurement, PO#: D1009036", texto)
    verificar("respuesta RE: no crea OC", r, None)
    r = cuerpo_es_orden_de_compra("Promociones de septiembre", "Compra hoy y ahorra 2026")
    verificar("correo cualquiera no crea OC", r, None)


def casos_pdf(carpeta: Path):
    verdad = json.loads((carpeta / "verdad.json").read_text(encoding="utf-8"))
    print(f"== PDFs reales ({len(verdad)})")
    for caso in verdad:
        ruta = carpeta / f"{caso['id']}.pdf"
        with pdfplumber.open(ruta) as pdf:
            texto = "".join((p.extract_text() or "") for p in pdf.pages)
            palabras = pdf.pages[0].extract_words() if pdf.pages else []
        es_oc = es_documento_oc(texto, caso["nombre"], caso.get("asunto"))
        esperado_oc = caso["numero"] is not None
        if es_oc != esperado_oc:
            verificar(f"[{caso['id']}] es OC? {caso['nombre'][:30]}", es_oc, esperado_oc)
            continue
        if not es_oc:
            verificar(f"[{caso['id']}] no-OC {caso['nombre'][:34]}", es_oc, False)
            continue
        r = detectar_numero_oc(texto_pdf=texto, palabras_pdf=palabras,
                               asunto=caso.get("asunto"), nombre_archivo=caso["nombre"])
        verificar(f"[{caso['id']}] {caso['nombre'][:38]}", r.numero, caso["numero"])


if __name__ == "__main__":
    casos_de_texto()
    if len(sys.argv) > 1:
        casos_pdf(Path(sys.argv[1]))
    print(f"\n{'TODO BIEN' if not fallos else f'{fallos} FALLO(S)'}")
    sys.exit(1 if fallos else 0)
