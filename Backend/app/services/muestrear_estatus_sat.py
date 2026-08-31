r"""
Paso 4 de la fase SAT - Monsort

Toma una muestra de facturas de la base, las consulta contra el webservice
de estatus del SAT, e imprime:

  1. Una linea por factura con el resultado
  2. Un resumen del catalogo REAL de valores distintos que devolvio el SAT

Ese catalogo es el input para disenar las columnas espejo de la migracion.

SOLO LEE. No escribe absolutamente nada en la base de datos.

Ubicacion sugerida: app/services/muestrear_estatus_sat.py

Uso (desde Backend/):

    # 15 facturas de origen gmail (las que tienen XML y por tanto 'fe')
    python -m app.services.muestrear_estatus_sat --origen gmail --limite 15

    # EXPERIMENTO: las mismas, pero omitiendo 'fe'.
    # Si funcionan, las 718 del Excel tambien son consultables.
    python -m app.services.muestrear_estatus_sat --origen gmail --limite 5 --sin-fe

    # Las del Excel: no tienen XML, asi que van forzosamente sin 'fe'
    python -m app.services.muestrear_estatus_sat --origen excel --limite 10

    # Una sola factura concreta
    python -m app.services.muestrear_estatus_sat --uuid CDDA6C1B-...
"""

from __future__ import annotations

import argparse
import time
from collections import Counter
from decimal import Decimal, InvalidOperation

import requests
from lxml import etree

# ---------------------------------------------------------------------------
# >>> AJUSTA SI TUS NOMBRES DIFIEREN <<<
# ---------------------------------------------------------------------------

COL_FOLIO = "folio_fiscal"     # columna del UUID en Facturas
COL_TOTAL = "total"            # columna del total en Facturas
COL_XML = "xml_factura"        # columna binaria del XML en Facturas
COL_ORIGEN = "origen"          # columna 'gmail'/'excel'/'sat'/'manual'
COL_ID_CLIENTE = "id_cliente"  # FK a Clientes
COL_RFC_CLIENTE = "rfc"        # columna del RFC en Clientes

RFC_EMISOR = "MSF140227BF7"    # Monsort, siempre el emisor

# ---------------------------------------------------------------------------
# Constantes del servicio
# ---------------------------------------------------------------------------

URL_SAT = "https://consultaqr.facturaelectronica.sat.gob.mx/ConsultaCFDIService.svc"
SOAP_ACTION = "http://tempuri.org/IConsultaCFDIService/Consulta"
NS_SOAP = "http://schemas.xmlsoap.org/soap/envelope/"
NS_RESPUESTA = "http://schemas.datacontract.org/2004/07/Sat.Cfdi.Negocio.ConsultaCfdi.Servicio"
NS_TFD = "http://www.sat.gob.mx/TimbreFiscalDigital"

ORDEN_CON_FE = ["re", "rr", "tt", "id", "fe"]
ORDEN_SIN_FE = ["re", "rr", "tt", "id"]

CAMPOS_RESPUESTA = [
    "CodigoEstatus",
    "EsCancelable",
    "Estado",
    "EstatusCancelacion",
    "ValidacionEFOS",
]

PAUSA = 2.0        # segundos entre llamadas; nunca en rafaga
TIEMPO_ESPERA = 30


# ---------------------------------------------------------------------------
# Acceso a datos (solo lectura)
# ---------------------------------------------------------------------------

def cargar_muestra(origen: str | None, limite: int, uuid: str | None) -> list[dict]:
    """
    Devuelve una lista de dicts con lo minimo para armar la expresion.
    Se hace join explicito contra Clientes en vez de usar el relationship,
    para no depender de como se llame el atributo en el modelo.
    """
    # Importar todos los modelos para que SQLAlchemy resuelva los mappers.
    from app.modelos import (  # noqa: F401
        usuario, factura, estados, configuracion, conceptos,
        complemento_pago, orden_compra, cp_documento_relacionado,
        correo_procesado, cliente,
    )
    from app.BaseDeDatos import SessionLocal
    from app.modelos.factura import Facturas
    from app.modelos.cliente import Cliente

    db = SessionLocal()
    try:
        consulta = db.query(
            getattr(Facturas, COL_FOLIO).label("folio"),
            getattr(Facturas, COL_TOTAL).label("total"),
            getattr(Facturas, COL_XML).label("xml"),
            getattr(Facturas, COL_ORIGEN).label("origen"),
            getattr(Cliente, COL_RFC_CLIENTE).label("rfc_receptor"),
        ).outerjoin(
            Cliente,
            getattr(Facturas, COL_ID_CLIENTE) == Cliente.id,
        )

        if uuid:
            consulta = consulta.filter(getattr(Facturas, COL_FOLIO).ilike(uuid))
        else:
            consulta = consulta.filter(getattr(Facturas, COL_FOLIO).isnot(None))
            if origen and origen != "todos":
                consulta = consulta.filter(getattr(Facturas, COL_ORIGEN) == origen)
            consulta = consulta.limit(limite)

        filas = consulta.all()
        return [
            {
                "folio": f.folio,
                "total": f.total,
                "xml": bytes(f.xml) if f.xml else None,
                "origen": f.origen,
                "rfc_receptor": f.rfc_receptor,
            }
            for f in filas
        ]
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Construccion de la expresion
# ---------------------------------------------------------------------------

def datos_desde_xml(xml_bytes: bytes) -> dict[str, str]:
    """Extrae re, rr, tt, id, fe del XML. Fuente preferida: es lo timbrado."""
    raiz = etree.fromstring(xml_bytes)
    ns_cfdi = etree.QName(raiz).namespace

    emisor = raiz.find(f"{{{ns_cfdi}}}Emisor")
    receptor = raiz.find(f"{{{ns_cfdi}}}Receptor")
    timbre = raiz.find(f".//{{{NS_TFD}}}TimbreFiscalDigital")
    sello = raiz.get("Sello") or ""

    if emisor is None or receptor is None or timbre is None or not sello:
        raise ValueError("XML incompleto (falta Emisor, Receptor, Timbre o Sello)")

    return {
        "re": emisor.get("Rfc") or "",
        "rr": receptor.get("Rfc") or "",
        "tt": formatear_total(raiz.get("Total") or ""),
        "id": timbre.get("UUID") or "",
        "fe": sello[-8:],
    }


def datos_desde_columnas(fila: dict) -> dict[str, str]:
    """
    Fallback para facturas sin XML (las 718 del Excel).
    No hay sello, asi que no hay 'fe'.
    """
    if not fila["rfc_receptor"]:
        raise ValueError("factura sin id_cliente: no se puede obtener el RFC receptor")
    if fila["total"] is None:
        raise ValueError("factura sin total")

    return {
        "re": RFC_EMISOR,
        "rr": fila["rfc_receptor"],
        "tt": formatear_total(str(fila["total"])),
        "id": fila["folio"],
    }


def formatear_total(valor: str) -> str:
    """4 decimales, confirmado contra el QR real en el paso 2."""
    try:
        return f"{Decimal(valor):.4f}"
    except (InvalidOperation, ValueError):
        return valor


def construir_expresion(valores: dict[str, str], incluir_fe: bool) -> str:
    orden = ORDEN_CON_FE if (incluir_fe and "fe" in valores) else ORDEN_SIN_FE
    return "?" + "&".join(f"{c}={valores[c]}" for c in orden)


# ---------------------------------------------------------------------------
# Llamada al SAT
# ---------------------------------------------------------------------------

def construir_envelope(expresion: str) -> bytes:
    return (
        '<soapenv:Envelope '
        'xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/" '
        'xmlns:tem="http://tempuri.org/">'
        "<soapenv:Header/><soapenv:Body><tem:Consulta>"
        f"<tem:expresionImpresa><![CDATA[{expresion}]]></tem:expresionImpresa>"
        "</tem:Consulta></soapenv:Body></soapenv:Envelope>"
    ).encode("utf-8")


def consultar(expresion: str) -> dict[str, str | None]:
    cabeceras = {
        "Content-Type": 'text/xml;charset="utf-8"',
        "SOAPAction": SOAP_ACTION,
        "Accept": "text/xml",
    }
    respuesta = requests.post(
        URL_SAT,
        data=construir_envelope(expresion),
        headers=cabeceras,
        timeout=TIEMPO_ESPERA,
    )
    raiz = etree.fromstring(respuesta.content)

    falla = raiz.find(f".//{{{NS_SOAP}}}Fault")
    if falla is not None:
        texto = falla.find(".//faultstring")
        raise RuntimeError(f"SOAP Fault: {texto.text if texto is not None else '?'}")

    return {
        campo: (
            nodo.text
            if (nodo := raiz.find(f".//{{{NS_RESPUESTA}}}{campo}")) is not None
            else None
        )
        for campo in CAMPOS_RESPUESTA
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Muestrea facturas y resume el catalogo real de "
                    "valores que devuelve el SAT."
    )
    parser.add_argument("--origen", default="gmail",
                        choices=["gmail", "excel", "sat", "manual", "todos"])
    parser.add_argument("--limite", type=int, default=15)
    parser.add_argument("--uuid", help="Consultar una sola factura")
    parser.add_argument("--sin-fe", action="store_true",
                        help="Omitir 'fe' aunque haya XML (experimento)")
    args = parser.parse_args()

    filas = cargar_muestra(args.origen, args.limite, args.uuid)
    if not filas:
        raise SystemExit(f"No se encontraron facturas con origen='{args.origen}'.")

    incluir_fe = not args.sin_fe

    print()
    print("=" * 78)
    print(f"MUESTREO DE ESTATUS SAT   ({len(filas)} facturas, "
          f"{'con' if incluir_fe else 'SIN'} parametro 'fe')")
    print("=" * 78)
    print()
    print(f"{'FOLIO':<38} {'ORIG':<6} {'ESTADO':<14} {'CANCELABLE'}")
    print("-" * 78)

    catalogo: dict[str, Counter] = {c: Counter() for c in CAMPOS_RESPUESTA}
    fallos: list[tuple[str, str]] = []
    exitos = 0
    sin_xml = 0

    for fila in filas:
        folio = fila["folio"] or "(sin folio)"
        try:
            if fila["xml"]:
                valores = datos_desde_xml(fila["xml"])
            else:
                sin_xml += 1
                valores = datos_desde_columnas(fila)

            campos = consultar(construir_expresion(valores, incluir_fe))

            estado = campos.get("Estado") or "-"
            cancelable = campos.get("EsCancelable") or "-"
            print(f"{folio:<38} {(fila['origen'] or '-'):<6} "
                  f"{estado:<14} {cancelable}")

            for clave, valor in campos.items():
                catalogo[clave][repr(valor)] += 1
            if estado in {"Vigente", "Cancelado"}:
                exitos += 1
            else:
                fallos.append((folio, f"Estado={estado!r}"))

        except Exception as error:
            print(f"{folio:<38} {(fila['origen'] or '-'):<6} "
                  f"[ERROR] {type(error).__name__}")
            fallos.append((folio, f"{type(error).__name__}: {error}"))

        time.sleep(PAUSA)

    # ------------------------- resumen -------------------------
    print()
    print("=" * 78)
    print("CATALOGO DE VALORES DEVUELTOS  (esto define las columnas espejo)")
    print("=" * 78)
    for campo in CAMPOS_RESPUESTA:
        print(f"\n  {campo}")
        if not catalogo[campo]:
            print("    (sin datos)")
            continue
        for valor, veces in catalogo[campo].most_common():
            largo = len(valor) - 2 if valor != "None" else 0
            print(f"    {veces:>4}x  {valor:<52} (largo {largo})")

    print()
    print("=" * 78)
    print("RESUMEN")
    print("=" * 78)
    print(f"  Consultadas:            {len(filas)}")
    print(f"  Estado concluyente:     {exitos}")
    print(f"  Fallos / no encontrado: {len(fallos)}")
    if sin_xml:
        print(f"  Sin XML (via columnas): {sin_xml}")

    if fallos:
        print()
        print("  Detalle de fallos:")
        for folio, motivo in fallos[:20]:
            print(f"    {folio}  ->  {motivo}")
        if len(fallos) > 20:
            print(f"    ... y {len(fallos) - 20} mas")

    if not incluir_fe:
        print()
        print("  EXPERIMENTO SIN 'fe':")
        if exitos and not fallos:
            print("    El SAT NO requiere 'fe'. Las 718 del Excel son consultables.")
        elif exitos:
            print("    Resultados mixtos. Revisa el detalle de fallos arriba.")
        else:
            print("    El SAT SI requiere 'fe'. Solo las facturas con XML")
            print("    se pueden verificar.")
    print()


if __name__ == "__main__":
    main()