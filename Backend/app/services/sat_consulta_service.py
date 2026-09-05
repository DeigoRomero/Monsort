r"""
Servicio de consulta de estatus de CFDI ante el SAT.

FUNCIONES PURAS. No recibe Session, no toca la base de datos, no importa
modelos. Se puede probar mockeando unicamente requests.post.

Reglas descubiertas empiricamente durante el paso 3 y el barrido historico
de 718 facturas:

  1. El orden de los parametros es: re, rr, tt, id, fe
  2. 'tt' se formatea a 4 decimales (convencion del PAC, pero el SAT
     tambien acepta 2; usamos 4 por consistencia con el QR)
  3. 'fe' es OPCIONAL. Sin XML se puede consultar igual.
  4. El CDATA no es opcional: la expresion lleva '&' sin escapar.
  5. CodigoEstatus es el GUARDIAN. Cuando no empieza con "S", el SAT
     igual devuelve EsCancelable y EstatusCancelacion poblados con
     valores que PARECEN legitimos pero son basura. Verificado con
     13C4F72D-404D-4937-BE50-8E9E92D2C9B1, que devolvio
     Estado='No Encontrado' junto con EstatusCancelacion='Plazo vencido'.

Ubicacion: app/services/sat_consulta_service.py
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

import requests
from lxml import etree

# ---------------------------------------------------------------------------
# Constantes del servicio
# ---------------------------------------------------------------------------

URL_SAT = "https://consultaqr.facturaelectronica.sat.gob.mx/ConsultaCFDIService.svc"
SOAP_ACTION = "http://tempuri.org/IConsultaCFDIService/Consulta"

NS_SOAP = "http://schemas.xmlsoap.org/soap/envelope/"
NS_RESPUESTA = (
    "http://schemas.datacontract.org/2004/07/Sat.Cfdi.Negocio.ConsultaCfdi.Servicio"
)

ORDEN_CON_FE = ("re", "rr", "tt", "id", "fe")
ORDEN_SIN_FE = ("re", "rr", "tt", "id")

TIEMPO_ESPERA = 30  # segundos

# Estados que el SAT considera una respuesta concluyente sobre la factura.
ESTADO_VIGENTE = "Vigente"
ESTADO_CANCELADO = "Cancelado"
ESTADOS_CONCLUYENTES = frozenset({ESTADO_VIGENTE, ESTADO_CANCELADO})


# ---------------------------------------------------------------------------
# Resultado
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ResultadoSat:
    """
    Respuesta del webservice ya filtrada por la regla del guardian.

    Si 'exitoso' es False, los cinco campos del SAT vienen en None a
    proposito: la respuesta cruda puede traer valores contaminados y no
    queremos que lleguen a la base de datos por accidente.
    """

    exitoso: bool
    codigo_estatus: str | None = None
    estado: str | None = None
    es_cancelable: str | None = None
    estatus_cancelacion: str | None = None
    validacion_efos: str | None = None
    motivo_fallo: str | None = None
    expresion: str | None = None

    @property
    def es_concluyente(self) -> bool:
        """True solo si el SAT afirmo algo utilizable sobre la factura."""
        return self.exitoso and self.estado in ESTADOS_CONCLUYENTES

    @property
    def esta_cancelada(self) -> bool:
        """
        True unicamente cuando el SAT afirma explicitamente 'Cancelado'.
        'No Encontrado' NUNCA cuenta como cancelada.
        """
        return self.es_concluyente and self.estado == ESTADO_CANCELADO


# ---------------------------------------------------------------------------
# Construccion de la expresion impresa
# ---------------------------------------------------------------------------

def formatear_total(total) -> str:
    """
    Formatea a 4 decimales usando Decimal.

    Nunca float: float("29450.67") no es exactamente 29450.67 en binario,
    y no queremos que la expresion impresa dependa de "probablemente
    no se nota".
    """
    try:
        return f"{Decimal(str(total)):.4f}"
    except (InvalidOperation, ValueError, TypeError) as error:
        raise ValueError(f"total no convertible a Decimal: {total!r}") from error


def construir_expresion(
    rfc_emisor: str,
    rfc_receptor: str,
    total,
    uuid: str,
    sello_ultimos_8: str | None = None,
) -> str:
    """
    Arma la expresion impresa (el contenido del QR de la representacion
    impresa). Sin URL-encoding: '&', '=' y el padding '==' de 'fe' van
    crudos.
    """
    faltantes = [
        nombre
        for nombre, valor in (
            ("rfc_emisor", rfc_emisor),
            ("rfc_receptor", rfc_receptor),
            ("uuid", uuid),
        )
        if not valor
    ]
    if faltantes:
        raise ValueError(f"faltan datos para la expresion: {', '.join(faltantes)}")

    if total is None:
        raise ValueError("factura sin total: no es consultable")

    tt = formatear_total(total)
    if Decimal(tt) <= 0:
        raise ValueError(f"total en cero o negativo ({tt}): no es consultable")

    valores = {
        "re": rfc_emisor.strip().upper(),
        "rr": rfc_receptor.strip().upper(),
        "tt": tt,
        "id": uuid.strip().upper(),
    }
    orden = ORDEN_SIN_FE
    if sello_ultimos_8:
        valores["fe"] = sello_ultimos_8
        orden = ORDEN_CON_FE

    return "?" + "&".join(f"{clave}={valores[clave]}" for clave in orden)


def construir_envelope(expresion: str) -> bytes:
    """
    El CDATA NO es opcional. La expresion lleva hasta cuatro '&' sin
    escapar; sin CDATA el parser del SAT los lee como inicio de entidad
    y rechaza el mensaje completo.
    """
    return (
        '<soapenv:Envelope '
        'xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/" '
        'xmlns:tem="http://tempuri.org/">'
        "<soapenv:Header/><soapenv:Body><tem:Consulta>"
        f"<tem:expresionImpresa><![CDATA[{expresion}]]></tem:expresionImpresa>"
        "</tem:Consulta></soapenv:Body></soapenv:Envelope>"
    ).encode("utf-8")


# ---------------------------------------------------------------------------
# Llamada
# ---------------------------------------------------------------------------

def _texto(raiz, campo: str) -> str | None:
    nodo = raiz.find(f".//{{{NS_RESPUESTA}}}{campo}")
    if nodo is None or nodo.text is None:
        return None
    texto = nodo.text.strip()
    return texto or None


def consultar_estatus(expresion: str) -> ResultadoSat:
    """
    Consulta el webservice y aplica la regla del guardian.

    Nunca lanza excepcion por fallo de red o respuesta invalida: devuelve
    un ResultadoSat con exitoso=False y el motivo. Asi el orquestador
    trata todos los fallos por el mismo camino.
    """
    cabeceras = {
        "Content-Type": 'text/xml;charset="utf-8"',
        "SOAPAction": SOAP_ACTION,
        "Accept": "text/xml",
    }

    try:
        respuesta = requests.post(
            URL_SAT,
            data=construir_envelope(expresion),
            headers=cabeceras,
            timeout=TIEMPO_ESPERA,
        )
    except requests.exceptions.RequestException as error:
        return ResultadoSat(
            exitoso=False,
            motivo_fallo=f"red: {type(error).__name__}: {error}",
            expresion=expresion,
        )

    if respuesta.status_code != 200:
        return ResultadoSat(
            exitoso=False,
            motivo_fallo=f"HTTP {respuesta.status_code}",
            expresion=expresion,
        )

    try:
        raiz = etree.fromstring(respuesta.content)
    except etree.XMLSyntaxError as error:
        return ResultadoSat(
            exitoso=False,
            motivo_fallo=f"respuesta no parseable: {error}",
            expresion=expresion,
        )

    falla = raiz.find(f".//{{{NS_SOAP}}}Fault")
    if falla is not None:
        detalle = falla.find(".//faultstring")
        texto = detalle.text if detalle is not None else "sin detalle"
        return ResultadoSat(
            exitoso=False,
            motivo_fallo=f"SOAP Fault: {texto}",
            expresion=expresion,
        )

    codigo = _texto(raiz, "CodigoEstatus")

    # ------------------------------------------------------------------
    # REGLA DEL GUARDIAN
    # Cuando CodigoEstatus no empieza con "S", los demas campos pueden
    # venir poblados con basura. Se descartan TODOS.
    # ------------------------------------------------------------------
    if not codigo or not codigo.startswith("S"):
        return ResultadoSat(
            exitoso=False,
            codigo_estatus=codigo,
            motivo_fallo=codigo or "sin CodigoEstatus",
            expresion=expresion,
        )

    return ResultadoSat(
        exitoso=True,
        codigo_estatus=codigo,
        estado=_texto(raiz, "Estado"),
        es_cancelable=_texto(raiz, "EsCancelable"),
        estatus_cancelacion=_texto(raiz, "EstatusCancelacion"),
        validacion_efos=_texto(raiz, "ValidacionEFOS"),
        expresion=expresion,
    )


# ---------------------------------------------------------------------------
# Atajo
# ---------------------------------------------------------------------------

def consultar(
    rfc_emisor: str,
    rfc_receptor: str,
    total,
    uuid: str,
    sello_ultimos_8: str | None = None,
) -> ResultadoSat:
    """Construye la expresion y consulta en un solo paso."""
    try:
        expresion = construir_expresion(
            rfc_emisor, rfc_receptor, total, uuid, sello_ultimos_8
        )
    except ValueError as error:
        # Ni siquiera se llama al SAT: la factura es inconsultable.
        return ResultadoSat(exitoso=False, motivo_fallo=f"datos: {error}")

    return consultar_estatus(expresion)