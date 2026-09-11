r"""
Cliente del servicio de Descarga Masiva de CFDI del SAT.

Este modulo define el CONTRATO (Protocol) y una implementacion FALSA que
permite construir y probar toda la maquina de estados sin tener la e.firma.

Cuando llegue la e.firma se agrega ClienteSATReal implementando el mismo
Protocol, y el orquestador no cambia una sola linea: recibe el cliente por
parametro. Es inyeccion de dependencias contra una interfaz.

El servicio del SAT es ASINCRONO y tiene cuatro operaciones:

  1. autenticar()          -> token, valido 5 minutos
  2. solicitar_descarga()  -> id_solicitud (NO los datos)
  3. verificar_solicitud() -> estado; hay que insistir hasta que este lista
  4. descargar_paquete()   -> ZIP con los XML

Dos ejes independientes en una solicitud:

  tipo_solicitud    metadata | cfdi        -> QUE formato se descarga
  tipo_comprobante  emitidos | recibidos   -> de QUIEN son los comprobantes

Para el SAT son operaciones distintas (SolicitaDescargaEmitidos vs
SolicitaDescargaRecibidos). Aqui se modela como parametro porque el
orquestador es el mismo.

Notas de realidad que el falso reproduce a proposito:

  - Verificar devuelve EN_PROCESO varias veces antes de LISTA. El tiempo
    real va de minutos a horas.
  - Codigo 5002: se agotaron las solicitudes de por vida para ese periodo.
    Solo aplica a tipo 'cfdi'; 'metadata' no tiene ese limite. Por eso TODO
    el desarrollo se hace contra metadata.
  - Codigo 5005: ya existe una solicitud identica en curso.
  - Una solicitud puede terminar sin CFDI (rango vacio). No es un error.

"""

from __future__ import annotations
import uuid as _uuid
import io
import random
import zipfile
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Protocol

# ---------------------------------------------------------------------------
# Vocabulario del servicio
# ---------------------------------------------------------------------------

# Valores de EstadoSolicitud que devuelve VerificaSolicitudDescarga
ESTADO_ACEPTADA = 1
ESTADO_EN_PROCESO = 2
ESTADO_TERMINADA = 3
ESTADO_ERROR = 4
ESTADO_RECHAZADA = 5
ESTADO_VENCIDA = 6

NOMBRE_ESTADO_SAT = {
    ESTADO_ACEPTADA: "Aceptada",
    ESTADO_EN_PROCESO: "En proceso",
    ESTADO_TERMINADA: "Terminada",
    ESTADO_ERROR: "Error",
    ESTADO_RECHAZADA: "Rechazada",
    ESTADO_VENCIDA: "Vencida",
}

# Codigos de estatus mas relevantes
CODIGO_ACEPTADA = "5000"
CODIGO_CUOTA_AGOTADA = "5002"
CODIGO_TOPE_MAXIMO = "5003"
CODIGO_DUPLICADA = "5005"
CODIGO_SIN_CFDI = "5004"

MENSAJE_CODIGO = {
    CODIGO_ACEPTADA: "Solicitud Aceptada",
    CODIGO_CUOTA_AGOTADA: "Se agoto las solicitudes de por vida",
    CODIGO_TOPE_MAXIMO: "Tope maximo",
    CODIGO_SIN_CFDI: "No se encontro la informacion",
    CODIGO_DUPLICADA: "Solicitud duplicada",
}

TIPO_METADATA = "metadata"
TIPO_CFDI = "cfdi"

# Coinciden con el server_default de SolicitudesSAT.tipo_comprobante.
# Plural masculino: "emitidos"/"recibidos", nunca "recibidas".
COMPROBANTE_EMITIDOS = "emitidos"
COMPROBANTE_RECIBIDOS = "recibidos"

# Un paquete trae hasta 10,000 comprobantes.
CFDIS_POR_PAQUETE = 10_000


# ---------------------------------------------------------------------------
# Respuestas
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class RespuestaSolicitud:
    """Resultado de SolicitaDescargaEmitidos / SolicitaDescargaRecibidos."""
    exitoso: bool
    id_solicitud: str | None = None
    codigo_estatus: str | None = None
    mensaje: str | None = None


@dataclass(frozen=True)
class RespuestaVerificacion:
    """Resultado de VerificaSolicitudDescarga."""
    exitoso: bool
    estado_solicitud: int | None = None
    codigo_estatus: str | None = None
    codigo_estado_solicitud: str | None = None
    numero_cfdis: int = 0
    mensaje: str | None = None
    paquetes: list[str] = field(default_factory=list)

    @property
    def esta_lista(self) -> bool:
        return self.exitoso and self.estado_solicitud == ESTADO_TERMINADA

    @property
    def sigue_en_curso(self) -> bool:
        return self.exitoso and self.estado_solicitud in (
            ESTADO_ACEPTADA, ESTADO_EN_PROCESO
        )

    @property
    def fracaso_definitivo(self) -> bool:
        """Estados de los que no se sale insistiendo."""
        return self.estado_solicitud in (
            ESTADO_ERROR, ESTADO_RECHAZADA, ESTADO_VENCIDA
        )


@dataclass(frozen=True)
class RespuestaPaquete:
    """Resultado de DescargarSolicitud."""
    exitoso: bool
    contenido: bytes | None = None
    codigo_estatus: str | None = None
    mensaje: str | None = None


# ---------------------------------------------------------------------------
# Contrato
# ---------------------------------------------------------------------------

class ClienteSAT(Protocol):
    """
    Contrato de las cuatro operaciones. El orquestador depende de esto,
    nunca de una implementacion concreta.
    """

    def autenticar(self) -> str:
        """Devuelve el token. Lanza excepcion si no puede autenticar."""
        ...

    def solicitar_descarga(
        self,
        fecha_inicial: date,
        fecha_final: date,
        tipo_solicitud: str = TIPO_METADATA,
        tipo_comprobante: str = COMPROBANTE_EMITIDOS,
    ) -> RespuestaSolicitud:
        ...

    def verificar_solicitud(self, id_solicitud: str) -> RespuestaVerificacion:
        ...

    def descargar_paquete(self, id_paquete: str) -> RespuestaPaquete:
        ...


# ---------------------------------------------------------------------------
# Implementacion falsa
# ---------------------------------------------------------------------------

class ClienteSATFalso:
    """
    Simula el servicio sin red y sin e.firma.

    Reproduce a proposito los casos feos, no solo el camino feliz: si solo
    simulara el exito, no estariamos probando la maquina de estados sino
    un if.

    IMPORTANTE: mantiene estado en memoria (solicitudes en curso, token).
    Se instancia UNA vez por ciclo completo, no una por paso.

    Parametros de control:
        verificaciones_antes_de_lista  cuantos polls devuelven EN_PROCESO
        cfdis_por_solicitud            cuantos comprobantes simular
        forzar_codigo                  fuerza un codigo de error concreto
                                       (CODIGO_CUOTA_AGOTADA, etc.)
        semilla                        para que las corridas sean repetibles
    """

    # Proveedores ficticios para las recibidas. Varios, para que el
    # endpoint de /emisores y el indice compuesto tengan algo que agrupar.
    PROVEEDORES = [
        ("AAA010101AAA", "SUMINISTROS INDUSTRIALES DEL NORTE SA DE CV"),
        ("BBB020202BB1", "TRANSPORTES Y LOGISTICA BAJA SA DE CV"),
        ("CCC030303CC2", "PAPELERIA Y EQUIPO DE OFICINA SA DE CV"),
        ("DDD040404DD3", "SERVICIOS PROFESIONALES TIJUANA SC"),
    ]

    # Mezcla realista: facturas, alguna nota de credito, algun complemento.
    # Sirve para probar que el endpoint filtre a "I" por defecto.
    EFECTOS = ["I", "I", "I", "I", "I", "I", "I", "E", "P", "P"]

    def __init__(
        self,
        rfc: str = "MSF140227BF7",
        verificaciones_antes_de_lista: int = 2,
        cfdis_por_solicitud: int = 25,
        forzar_codigo: str | None = None,
        semilla: int | None = 42,
    ):
        self.rfc = rfc
        self.verificaciones_antes_de_lista = verificaciones_antes_de_lista
        self.cfdis_por_solicitud = cfdis_por_solicitud
        self.forzar_codigo = forzar_codigo
        self._azar = random.Random(semilla)

        # Estado interno: emula la memoria del servidor del SAT
        self._solicitudes: dict[str, dict] = {}
        self._token: str | None = None
        self._token_expira: datetime | None = None
        self._contador = 0

    # -------------------- autenticacion --------------------

    def autenticar(self) -> str:
        ahora = datetime.now(timezone.utc)
        if self._token and self._token_expira and ahora < self._token_expira:
            return self._token

        self._token = f"FALSO-{self._azar.randrange(10**12):012d}"
        # El token real dura 5 minutos.
        self._token_expira = ahora + timedelta(minutes=5)
        return self._token

    # -------------------- solicitud --------------------

    def solicitar_descarga(
        self,
        fecha_inicial: date,
        fecha_final: date,
        tipo_solicitud: str = TIPO_METADATA,
        tipo_comprobante: str = COMPROBANTE_EMITIDOS,
    ) -> RespuestaSolicitud:
        if fecha_inicial > fecha_final:
            return RespuestaSolicitud(
                exitoso=False,
                codigo_estatus="5001",
                mensaje="La fecha inicial no puede ser mayor a la final",
            )

        if tipo_comprobante not in (COMPROBANTE_EMITIDOS, COMPROBANTE_RECIBIDOS):
            return RespuestaSolicitud(
                exitoso=False,
                codigo_estatus="5001",
                mensaje=f"tipo_comprobante invalido: {tipo_comprobante!r}",
            )

        if self.forzar_codigo:
            return RespuestaSolicitud(
                exitoso=False,
                codigo_estatus=self.forzar_codigo,
                mensaje=MENSAJE_CODIGO.get(self.forzar_codigo, "Error simulado"),
            )

        # Duplicada: mismo rango, tipo y orientacion aun en curso.
        # tipo_comprobante entra en la clave: pedir emitidos y recibidos
        # del mismo rango son solicitudes distintas, no una duplicada.
        clave = (fecha_inicial, fecha_final, tipo_solicitud, tipo_comprobante)
        for datos in self._solicitudes.values():
            if datos["clave"] == clave and datos["estado"] != ESTADO_TERMINADA:
                return RespuestaSolicitud(
                    exitoso=False,
                    codigo_estatus=CODIGO_DUPLICADA,
                    mensaje=MENSAJE_CODIGO[CODIGO_DUPLICADA],
                )

        self._contador += 1
        id_solicitud = f"fa1b2c3d-0000-0000-0000-{_uuid.uuid4().int % 10**12:012d}"

        self._solicitudes[id_solicitud] = {
            "clave": clave,
            "tipo": tipo_solicitud,
            "comprobante": tipo_comprobante,
            "estado": ESTADO_ACEPTADA,
            "verificaciones": 0,
            "cfdis": self.cfdis_por_solicitud,
            "paquetes": [],
        }

        return RespuestaSolicitud(
            exitoso=True,
            id_solicitud=id_solicitud,
            codigo_estatus=CODIGO_ACEPTADA,
            mensaje=MENSAJE_CODIGO[CODIGO_ACEPTADA],
        )

    # -------------------- verificacion --------------------

    def verificar_solicitud(self, id_solicitud: str) -> RespuestaVerificacion:
        datos = self._solicitudes.get(id_solicitud)
        if datos is None:
            return RespuestaVerificacion(
                exitoso=False,
                codigo_estatus="5001",
                mensaje="No existe la solicitud",
            )

        datos["verificaciones"] += 1

        if datos["verificaciones"] <= self.verificaciones_antes_de_lista:
            datos["estado"] = ESTADO_EN_PROCESO
            return RespuestaVerificacion(
                exitoso=True,
                estado_solicitud=ESTADO_EN_PROCESO,
                codigo_estatus=CODIGO_ACEPTADA,
                mensaje=NOMBRE_ESTADO_SAT[ESTADO_EN_PROCESO],
            )

        # Rango sin comprobantes: termina bien, pero sin paquetes.
        if datos["cfdis"] == 0:
            datos["estado"] = ESTADO_TERMINADA
            return RespuestaVerificacion(
                exitoso=True,
                estado_solicitud=ESTADO_TERMINADA,
                codigo_estatus=CODIGO_SIN_CFDI,
                numero_cfdis=0,
                mensaje=MENSAJE_CODIGO[CODIGO_SIN_CFDI],
                paquetes=[],
            )

        if not datos["paquetes"]:
            total = datos["cfdis"]
            cantidad = max(1, -(-total // CFDIS_POR_PAQUETE))  # techo
            datos["paquetes"] = [
                f"{id_solicitud}_{indice:02d}" for indice in range(1, cantidad + 1)
            ]

        datos["estado"] = ESTADO_TERMINADA
        return RespuestaVerificacion(
            exitoso=True,
            estado_solicitud=ESTADO_TERMINADA,
            codigo_estatus=CODIGO_ACEPTADA,
            codigo_estado_solicitud=CODIGO_ACEPTADA,
            numero_cfdis=datos["cfdis"],
            mensaje=NOMBRE_ESTADO_SAT[ESTADO_TERMINADA],
            paquetes=list(datos["paquetes"]),
        )

    # -------------------- descarga --------------------

    def descargar_paquete(self, id_paquete: str) -> RespuestaPaquete:
        id_solicitud = id_paquete.rsplit("_", 1)[0]
        datos = self._solicitudes.get(id_solicitud)

        if datos is None or id_paquete not in datos["paquetes"]:
            return RespuestaPaquete(
                exitoso=False,
                codigo_estatus="5001",
                mensaje="No existe el paquete",
            )

        if datos["tipo"] == TIPO_METADATA:
            contenido = self._zip_metadata(datos["cfdis"], datos["comprobante"])
        else:
            contenido = self._zip_cfdi(datos["cfdis"])

        return RespuestaPaquete(
            exitoso=True,
            contenido=contenido,
            codigo_estatus=CODIGO_ACEPTADA,
            mensaje=MENSAJE_CODIGO[CODIGO_ACEPTADA],
        )

    # -------------------- generacion de contenido --------------------

    def _uuid_falso(self, indice: int) -> str:
        base = f"{indice:032x}".upper()
        return f"{base[:8]}-{base[8:12]}-{base[12:16]}-{base[16:20]}-{base[20:32]}"

    def _zip_metadata(
        self,
        cuantos: int,
        tipo_comprobante: str = COMPROBANTE_EMITIDOS,
    ) -> bytes:
        """
        El paquete de metadata trae un unico .txt delimitado por '~',
        con encabezado. Es texto, no XML.

        La orientacion invierte emisor y receptor:
          emitidos   Monsort emite  -> RfcEmisor = Monsort
          recibidos  un proveedor le emite a Monsort -> RfcReceptor = Monsort
        """
        columnas = [
            "Uuid", "RfcEmisor", "NombreEmisor", "RfcReceptor", "NombreReceptor",
            "RfcPac", "FechaEmision", "FechaCertificacionSat", "Monto",
            "EfectoComprobante", "Estatus", "FechaCancelacion",
        ]
        lineas = ["~".join(columnas)]

        es_recibidos = tipo_comprobante == COMPROBANTE_RECIBIDOS

        for indice in range(1, cuantos + 1):
            cancelado = indice % 10 == 0

            if es_recibidos:
                rfc_prov, nombre_prov = self.PROVEEDORES[
                    indice % len(self.PROVEEDORES)
                ]
                rfc_emisor, nombre_emisor = rfc_prov, nombre_prov
                rfc_receptor = self.rfc
                nombre_receptor = "MONSORT SIN FRONTERAS SA DE CV"
                efecto = self.EFECTOS[indice % len(self.EFECTOS)]
            else:
                rfc_emisor = self.rfc
                nombre_emisor = "MONSORT SIN FRONTERAS SA DE CV"
                rfc_receptor = "PIN040713FL9"
                nombre_receptor = "CLIENTE DE PRUEBA SA DE CV"
                efecto = "I"

            lineas.append("~".join([
                self._uuid_falso(indice),
                rfc_emisor,
                nombre_emisor,
                rfc_receptor,
                nombre_receptor,
                "PPD101129EA3",
                f"2026-0{(indice % 9) + 1}-15T10:00:00",
                f"2026-0{(indice % 9) + 1}-15T10:05:00",
                f"{1000 + indice * 37}.00",
                efecto,
                "0" if cancelado else "1",
                "2026-08-01T00:00:00" if cancelado else "",
            ]))

        memoria = io.BytesIO()
        with zipfile.ZipFile(memoria, "w", zipfile.ZIP_DEFLATED) as paquete:
            paquete.writestr(
                "metadata.txt", "\n".join(lineas).encode("utf-8")
            )
        return memoria.getvalue()

    def _zip_cfdi(self, cuantos: int) -> bytes:
        """El paquete de CFDI trae un .xml por comprobante."""
        memoria = io.BytesIO()
        with zipfile.ZipFile(memoria, "w", zipfile.ZIP_DEFLATED) as paquete:
            for indice in range(1, cuantos + 1):
                uuid = self._uuid_falso(indice)
                paquete.writestr(f"{uuid}.xml", self._xml_falso(uuid, indice))
        return memoria.getvalue()

    def _xml_falso(self, uuid: str, indice: int) -> bytes:
        """
        CFDI 4.0 minimo pero estructuralmente valido: lo que importa es que
        extraer_datos_xml() lo pueda parsear sin cambios.
        """
        subtotal = 1000 + indice * 37
        iva = round(subtotal * 0.16, 2)
        total = round(subtotal + iva, 2)
        mes = (indice % 9) + 1

        return f"""<?xml version="1.0" encoding="UTF-8"?>
<cfdi:Comprobante xmlns:cfdi="http://www.sat.gob.mx/cfd/4"
 xmlns:tfd="http://www.sat.gob.mx/TimbreFiscalDigital"
 Version="4.0" Serie="A" Folio="{indice}"
 Fecha="2026-0{mes}-15T10:00:00" Sello="SELLOFALSO{indice:04d}RprbFg=="
 FormaPago="03" NoCertificado="00001000000517971656"
 SubTotal="{subtotal}.00" Moneda="MXN" Total="{total}"
 TipoDeComprobante="I" Exportacion="01" MetodoPago="PPD"
 LugarExpedicion="22000">
  <cfdi:Emisor Rfc="{self.rfc}" Nombre="MONSORT SIN FRONTERAS SA DE CV"
   RegimenFiscal="601"/>
  <cfdi:Receptor Rfc="PIN040713FL9" Nombre="CLIENTE DE PRUEBA SA DE CV"
   DomicilioFiscalReceptor="22000" RegimenFiscalReceptor="601" UsoCFDI="G03"/>
  <cfdi:Conceptos>
    <cfdi:Concepto ClaveProdServ="01010101" Cantidad="1" ClaveUnidad="H87"
     Descripcion="Servicio de prueba OC {70000 + indice}"
     ValorUnitario="{subtotal}.00" Importe="{subtotal}.00" ObjetoImp="02"/>
  </cfdi:Conceptos>
  <cfdi:Impuestos TotalImpuestosTrasladados="{iva}"/>
  <cfdi:Complemento>
    <tfd:TimbreFiscalDigital Version="1.1" UUID="{uuid}"
     FechaTimbrado="2026-0{mes}-15T10:05:00"
     RfcProvCertif="PPD101129EA3"
     SelloCFD="SELLOFALSO{indice:04d}RprbFg=="
     SelloSAT="SELLOSATFALSO{indice:04d}0HG6OQ=="
     NoCertificadoSAT="00001000000504465028"/>
  </cfdi:Complemento>
</cfdi:Comprobante>
""".encode("utf-8")


# ---------------------------------------------------------------------------
# Demostracion
# ---------------------------------------------------------------------------

def _demostracion() -> None:
    """Recorre el ciclo completo contra el cliente falso."""
    cliente = ClienteSATFalso(cfdis_por_solicitud=5)

    print()
    print("=" * 70)
    print("CICLO COMPLETO CONTRA EL CLIENTE FALSO")
    print("=" * 70)

    print(f"\n1. autenticar()  ->  {cliente.autenticar()}")

    inicio, fin = date(2026, 1, 1), date(2026, 1, 31)
    solicitud = cliente.solicitar_descarga(inicio, fin, TIPO_METADATA)
    print(f"\n2. solicitar_descarga({inicio}, {fin}, metadata, emitidos)")
    print(f"   exitoso      = {solicitud.exitoso}")
    print(f"   id_solicitud = {solicitud.id_solicitud}")
    print(f"   codigo       = {solicitud.codigo_estatus} - {solicitud.mensaje}")

    print("\n3. verificar_solicitud()  (insiste hasta que este lista)")
    for intento in range(1, 6):
        verificacion = cliente.verificar_solicitud(solicitud.id_solicitud)
        nombre = NOMBRE_ESTADO_SAT.get(verificacion.estado_solicitud, "?")
        print(f"   intento {intento}: estado={verificacion.estado_solicitud} "
              f"({nombre})  cfdis={verificacion.numero_cfdis} "
              f"paquetes={len(verificacion.paquetes)}")
        if verificacion.esta_lista:
            break

    print("\n4. descargar_paquete()")
    for id_paquete in verificacion.paquetes:
        paquete = cliente.descargar_paquete(id_paquete)
        print(f"   {id_paquete}: {len(paquete.contenido)} bytes")
        with zipfile.ZipFile(io.BytesIO(paquete.contenido)) as archivo:
            for nombre in archivo.namelist():
                print(f"      -> {nombre}")
                contenido = archivo.read(nombre).decode("utf-8")
                for linea in contenido.splitlines()[:3]:
                    print(f"         {linea[:100]}")

    # -------- recibidos: misma maquinaria, orientacion invertida --------
    print("\n5. Mismo ciclo con tipo_comprobante='recibidos'")
    sol_rec = cliente.solicitar_descarga(
        inicio, fin, TIPO_METADATA, COMPROBANTE_RECIBIDOS
    )
    print(f"   id_solicitud = {sol_rec.id_solicitud} "
          f"({sol_rec.codigo_estatus} - {sol_rec.mensaje})")

    for _ in range(5):
        ver_rec = cliente.verificar_solicitud(sol_rec.id_solicitud)
        if ver_rec.esta_lista:
            break

    for id_paquete in ver_rec.paquetes:
        paquete = cliente.descargar_paquete(id_paquete)
        with zipfile.ZipFile(io.BytesIO(paquete.contenido)) as archivo:
            for nombre in archivo.namelist():
                contenido = archivo.read(nombre).decode("utf-8")
                for linea in contenido.splitlines()[:4]:
                    print(f"      {linea[:110]}")

    print("\n6. Casos de error")
    # Duplicada: dos solicitudes identicas sin que la primera termine.
    otro = ClienteSATFalso()
    otro.solicitar_descarga(inicio, fin, TIPO_METADATA)
    duplicada = otro.solicitar_descarga(inicio, fin, TIPO_METADATA)
    print(f"   solicitud duplicada:  {duplicada.codigo_estatus} - "
          f"{duplicada.mensaje}")

    # Mismo rango pero otra orientacion: NO es duplicada.
    no_duplicada = otro.solicitar_descarga(
        inicio, fin, TIPO_METADATA, COMPROBANTE_RECIBIDOS
    )
    print(f"   emitidos vs recibidos: exitoso={no_duplicada.exitoso} "
          f"({no_duplicada.codigo_estatus})")

    agotado = ClienteSATFalso(forzar_codigo=CODIGO_CUOTA_AGOTADA)
    respuesta = agotado.solicitar_descarga(inicio, fin, TIPO_CFDI)
    print(f"   cuota agotada:        {respuesta.codigo_estatus} - "
          f"{respuesta.mensaje}")

    vacio = ClienteSATFalso(cfdis_por_solicitud=0,
                            verificaciones_antes_de_lista=0)
    sol_vacia = vacio.solicitar_descarga(inicio, fin, TIPO_METADATA)
    ver_vacia = vacio.verificar_solicitud(sol_vacia.id_solicitud)
    print(f"   rango sin cfdis:      estado={ver_vacia.estado_solicitud} "
          f"codigo={ver_vacia.codigo_estatus} paquetes={len(ver_vacia.paquetes)}")

    print()
    print("=" * 70)
    print()


if __name__ == "__main__":
    _demostracion()