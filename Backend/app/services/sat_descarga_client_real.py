r"""
Cliente real del servicio de Descarga Masiva del SAT.

Implementa el mismo Protocol ClienteSAT que ClienteSATFalso, envolviendo
la libreria `cfdiclient`, que ya resuelve la parte dificil: la
autenticacion WS-Security con token SAML firmado con la e.firma.

    pip install cfdiclient

El orquestador (avanzar_solicitudes) no cambia una sola linea: recibe el
cliente por parametro.

TRAMPAS DE cfdiclient QUE ESTE ADAPTADOR RESUELVE

  1. estado_comprobante default es 'Vigente'. Con ese valor la descarga
     NO trae las canceladas, que es justo lo que a Monsort le interesa
     detectar. Aqui se fuerza 'Todos'.
  2. tipo_solicitud espera 'CFDI' / 'Metadata' con esas mayusculas
     exactas. El resto del proyecto usa minusculas.
  3. El token dura 300 segundos. Se cachea con margen para no pedir uno
     nuevo en cada llamada ni usar uno vencido.
  4. descargar_paquete devuelve base64 en 'paquete_b64', no bytes.
  5. estado_solicitud regresa como string; el orquestador compara contra
     enteros.

Ubicacion sugerida: app/services/sat_descarga_client_real.py
"""

from __future__ import annotations

import base64
import logging
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from cfdiclient import (
    Autenticacion,
    DescargaMasiva,
    Fiel,
    SolicitaDescargaEmitidos,
    SolicitaDescargaRecibidos,
    VerificaSolicitudDescarga,
)

from app.services.sat_descarga_client import (
    RespuestaPaquete,
    RespuestaSolicitud,
    RespuestaVerificacion,
    TIPO_CFDI,
    TIPO_METADATA,
)

logger = logging.getLogger(__name__)

# cfdiclient exige estas mayusculas exactas.
TIPO_SOLICITUD_SAT = {
    TIPO_METADATA: "Metadata",
    TIPO_CFDI: "CFDI",
}

# 'Todos' incluye vigentes Y canceladas. No usar el default 'Vigente'.
ESTADO_COMPROBANTE = "Todos"

# El token del SAT dura 300 s. Se renueva a los 240 para no apurarse.
VIGENCIA_TOKEN_SEGUNDOS = 240

TIEMPO_ESPERA = 30


class ClienteSATReal:
    """
    Implementacion real del Protocol ClienteSAT.

    Se instancia una vez y se reutiliza: mantiene el token en memoria.
    """

    def __init__(
        self,
        rfc: str,
        ruta_cer: str | Path,
        ruta_key: str | Path,
        contrasena: str,
        timeout: int = TIEMPO_ESPERA,
    ):
        self.rfc = rfc.strip().upper()
        self.timeout = timeout

        cer_der = Path(ruta_cer).read_bytes()
        key_der = Path(ruta_key).read_bytes()

        # Si la contrasena es incorrecta, falla aqui y no a media
        # maquina de estados.
        self.fiel = Fiel(cer_der, key_der, contrasena)

        self._token: str | None = None
        self._token_expira: datetime | None = None

    # ------------------------------------------------------------------
    # Autenticacion
    # ------------------------------------------------------------------

    def autenticar(self) -> str:
        ahora = datetime.now(timezone.utc)

        if self._token and self._token_expira and ahora < self._token_expira:
            return self._token

        autenticacion = Autenticacion(self.fiel, timeout=self.timeout)
        self._token = autenticacion.obtener_token()
        self._token_expira = ahora + timedelta(seconds=VIGENCIA_TOKEN_SEGUNDOS)

        logger.debug("Token del SAT renovado")
        return self._token

    # ------------------------------------------------------------------
    # Solicitud
    # ------------------------------------------------------------------

    def solicitar_descarga(
        self,
        fecha_inicial: date,
        fecha_final: date,
        tipo_solicitud: str = TIPO_METADATA,
        tipo_comprobante: str = "emitidos",
    ) -> RespuestaSolicitud:
        try:
            token = self.autenticar()
        except Exception as error:  # noqa: BLE001
            logger.exception("Fallo al autenticar ante el SAT")
            return RespuestaSolicitud(
                exitoso=False, mensaje=f"autenticacion: {error}"
            )

        # Emitidos: Monsort es el emisor. Recibidos: es el receptor.
        if tipo_comprobante == "recibidos":
            servicio = SolicitaDescargaRecibidos(self.fiel, timeout=self.timeout)
            filtro = {"rfc_receptor": self.rfc}
        else:
            servicio = SolicitaDescargaEmitidos(self.fiel, timeout=self.timeout)
            filtro = {"rfc_emisor": self.rfc}

        try:
            resultado = servicio.solicitar_descarga(
                token,
                self.rfc,
                fecha_inicial,
                fecha_final,
                tipo_solicitud=TIPO_SOLICITUD_SAT[tipo_solicitud],
                estado_comprobante=ESTADO_COMPROBANTE,
                **filtro,
            )
        except Exception as error:  # noqa: BLE001
            logger.exception("Fallo al solicitar descarga")
            return RespuestaSolicitud(exitoso=False, mensaje=str(error))

        codigo = resultado.get("cod_estatus")
        id_solicitud = resultado.get("id_solicitud")
        mensaje = resultado.get("mensaje")

        # 5000 es aceptada. Cualquier otro codigo es rechazo o error, y
        # el orquestador ya sabe distinguir cuales son terminales.
        if codigo == "5000" and id_solicitud:
            return RespuestaSolicitud(
                exitoso=True,
                id_solicitud=id_solicitud,
                codigo_estatus=codigo,
                mensaje=mensaje,
            )

        return RespuestaSolicitud(
            exitoso=False, codigo_estatus=codigo, mensaje=mensaje
        )

    # ------------------------------------------------------------------
    # Verificacion
    # ------------------------------------------------------------------

    def verificar_solicitud(self, id_solicitud: str) -> RespuestaVerificacion:
        try:
            token = self.autenticar()
        except Exception as error:  # noqa: BLE001
            logger.exception("Fallo al autenticar ante el SAT")
            return RespuestaVerificacion(
                exitoso=False, mensaje=f"autenticacion: {error}"
            )

        servicio = VerificaSolicitudDescarga(self.fiel, timeout=self.timeout)

        try:
            resultado = servicio.verificar_descarga(token, self.rfc, id_solicitud)
        except Exception as error:  # noqa: BLE001
            logger.exception("Fallo al verificar solicitud %s", id_solicitud)
            return RespuestaVerificacion(exitoso=False, mensaje=str(error))

        codigo = resultado.get("cod_estatus")

        # El SAT regresa todo como string.
        try:
            estado = int(resultado.get("estado_solicitud") or 0)
        except (TypeError, ValueError):
            estado = 0

        try:
            cfdis = int(resultado.get("numero_cfdis") or 0)
        except (TypeError, ValueError):
            cfdis = 0

        if codigo != "5000":
            return RespuestaVerificacion(
                exitoso=False,
                estado_solicitud=estado,
                codigo_estatus=codigo,
                mensaje=resultado.get("mensaje"),
            )

        return RespuestaVerificacion(
            exitoso=True,
            estado_solicitud=estado,
            codigo_estatus=codigo,
            codigo_estado_solicitud=resultado.get("codigo_estado_solicitud"),
            numero_cfdis=cfdis,
            mensaje=resultado.get("mensaje"),
            paquetes=list(resultado.get("paquetes") or []),
        )

    # ------------------------------------------------------------------
    # Descarga
    # ------------------------------------------------------------------

    def descargar_paquete(self, id_paquete: str) -> RespuestaPaquete:
        try:
            token = self.autenticar()
        except Exception as error:  # noqa: BLE001
            logger.exception("Fallo al autenticar ante el SAT")
            return RespuestaPaquete(
                exitoso=False, mensaje=f"autenticacion: {error}"
            )

        servicio = DescargaMasiva(self.fiel, timeout=self.timeout)

        try:
            resultado = servicio.descargar_paquete(token, self.rfc, id_paquete)
        except Exception as error:  # noqa: BLE001
            logger.exception("Fallo al descargar paquete %s", id_paquete)
            return RespuestaPaquete(exitoso=False, mensaje=str(error))

        codigo = resultado.get("cod_estatus")
        contenido_b64 = resultado.get("paquete_b64")

        if not contenido_b64:
            return RespuestaPaquete(
                exitoso=False,
                codigo_estatus=codigo,
                mensaje=resultado.get("mensaje") or "paquete vacio",
            )

        try:
            contenido = base64.b64decode(contenido_b64)
        except Exception as error:  # noqa: BLE001
            return RespuestaPaquete(
                exitoso=False, mensaje=f"base64 invalido: {error}"
            )

        return RespuestaPaquete(
            exitoso=True,
            contenido=contenido,
            codigo_estatus=codigo,
            mensaje=resultado.get("mensaje"),
        )


# ----------------------------------------------------------------------
# Fabrica
# ----------------------------------------------------------------------

def construir_cliente_sat(rfc: str = "MSF140227BF7"):
    """
    Devuelve ClienteSATReal si hay e.firma configurada, o None.

    El orquestador cae a ClienteSATFalso cuando recibe None, de modo que
    el sistema sigue operando sin credenciales.
    """
    from app.core.config import settings

    ruta_cer = getattr(settings, "SAT_CER_PATH", None)
    ruta_key = getattr(settings, "SAT_KEY_PATH", None)
    contrasena = getattr(settings, "SAT_KEY_PASSWORD", None)

    if not (ruta_cer and ruta_key and contrasena):
        return None

    if not Path(ruta_cer).exists() or not Path(ruta_key).exists():
        logger.error(
            "SAT_CER_PATH o SAT_KEY_PATH apuntan a archivos inexistentes"
        )
        return None

    try:
        return ClienteSATReal(rfc, ruta_cer, ruta_key, contrasena)
    except Exception:
        logger.exception("No se pudo construir ClienteSATReal")
        return None