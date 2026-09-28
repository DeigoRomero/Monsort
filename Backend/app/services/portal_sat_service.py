r"""
Sincronizacion de facturas RECIBIDAS con el portal de CFDI del SAT.

POR QUE EXISTE

    La Descarga Masiva del SAT es asincrona: se pide, se espera y entrega
    cuando quiere (en septiembre 2026, dias; a veces nunca). Para que la
    seccion de Recibidas este al dia se consulta el MISMO portal que usa el
    contador ("Consulta y recuperacion de comprobantes"), que responde al
    momento. Es como dejar de pedir el estado de cuenta por correo y entrar a
    la banca en linea.

COMO ESTA ARMADO

    Backend/sat_portal/consultar_portal.php   habla con el portal (libreria
                                              phpcfdi/cfdi-sat-scraper, PHP)
                                              y devuelve JSON. No toca la base.
    este modulo                               lo invoca con subprocess, valida,
                                              guarda y registra la corrida.

    La e.firma viaja por variables de entorno, nunca por argumentos.

QUE ESCRIBE EN FacturasRecibidas (origen='portal' si la fila es nueva)

    Hechos de emision (folio, emisor, fecha, monto)  solo al INSERTAR.
                                                     Nunca se pisan.
    Estatus (sat_estado, fecha_cancelacion)          se ACTUALIZAN: el portal
                                                     es el SAT mismo.
    Faltantes (efecto, PAC, nombre, XML)             se RELLENAN solo si estan
                                                     vacios (COALESCE). Asi se
                                                     completan las filas que
                                                     entraron del Excel.
    fecha_vista_portal                               siempre.

CUANDO CORRE (app/core/scheduler.py, solo si SAT_PORTAL_ACTIVO=true)

    Varias veces al dia: ultimos DIAS_VENTANA_RECIENTE dias. Se miran varios
    dias hacia atras porque un CFDI puede certificarse hasta 72 h despues de
    su fecha de emision, y el portal filtra recibidas por fecha de EMISION.
    Semanal: ultimos DIAS_BARRIDO dias, para detectar cancelaciones tardias.

    Nunca corren dos a la vez: candado de Postgres (pg_try_advisory_lock).

CLI (desde Backend/)

    python -m app.services.portal_sat_service --verificar-sesion
    python -m app.services.portal_sat_service --probar --desde 2026-09-01 --hasta 2026-09-01
    python -m app.services.portal_sat_service --probar --desde 2026-09-01 --hasta 2026-09-01 --tipo emitidos
    python -m app.services.portal_sat_service --desde 2026-04-01 --hasta 2026-09-28 --apply
    python -m app.services.portal_sat_service --estado

    --probar consulta el portal y compara contra la base SIN escribir nada.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from zoneinfo import ZoneInfo

from sqlalchemy import func, literal_column, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

ZONA_MEXICO = ZoneInfo("America/Mexico_City")

TIPO_RECIBIDOS = "recibidos"
TIPO_EMITIDOS = "emitidos"

MOTIVO_PROGRAMADA = "programada"
MOTIVO_BARRIDO = "barrido"
MOTIVO_MANUAL = "manual"
MOTIVO_CLI = "cli"

EN_CURSO = "EN_CURSO"
EXITOSA = "EXITOSA"
FALLIDA = "FALLIDA"

DIAS_VENTANA_RECIENTE = 5
DIAS_BARRIDO = 90
DIAS_MAXIMOS_POR_CORRIDA = 200       # el backfill de abril a hoy cabe de sobra

# Horas sin una corrida exitosa a partir de las cuales el frontend avisa.
HORAS_ALERTA = 8

# Candado global: numero arbitrario pero fijo. Si dos procesos lo piden, solo
# uno lo obtiene; el otro sale sin hacer nada.
LLAVE_CANDADO = 7_340_281_928

TAMANIO_LOTE = 200

EFECTOS = {
    "INGRESO": "I", "EGRESO": "E", "PAGO": "P",
    "TRASLADO": "T", "NOMINA": "N", "NÓMINA": "N",
}
ESTADOS = {"VIGENTE": "Vigente", "CANCELADO": "Cancelado"}

PATRON_UUID = re.compile(
    r"^[0-9A-F]{8}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{12}$"
)
PATRON_RFC = re.compile(r"^[A-ZÑ&]{3,4}\d{6}[A-Z0-9]{3}$")


# ---------------------------------------------------------------------------
# Errores
# ---------------------------------------------------------------------------

class ErrorPortal(Exception):
    """Fallo al consultar el portal. tipo: configuracion | credencial |
    login | portal | timeout | interno."""

    def __init__(self, tipo: str, mensaje: str):
        super().__init__(mensaje)
        self.tipo = tipo
        self.mensaje = mensaje


class SincronizacionEnCurso(Exception):
    """Ya hay otra corrida en marcha."""


# ---------------------------------------------------------------------------
# Utilidades
# ---------------------------------------------------------------------------

def _ahora() -> datetime:
    return datetime.now(timezone.utc)


def hoy_mexico() -> date:
    return datetime.now(ZONA_MEXICO).date()


def _ruta_script() -> Path:
    from app.core.config import settings

    if settings.SAT_PORTAL_SCRIPT:
        return Path(settings.SAT_PORTAL_SCRIPT)
    # app/services/este_archivo.py -> Backend/sat_portal/consultar_portal.php
    return Path(__file__).resolve().parents[2] / "sat_portal" / "consultar_portal.php"


def parsear_monto(valor) -> Decimal | None:
    """'$14,075.00' -> Decimal('14075.00'). El portal usa coma de miles."""
    texto = str(valor or "").strip().replace("$", "").replace(",", "").replace(" ", "")
    negativo = texto.startswith("-")
    texto = texto.lstrip("-")
    if not re.match(r"^\d+(\.\d+)?$", texto):
        return None
    try:
        monto = Decimal(texto)
    except InvalidOperation:
        return None
    return -monto if negativo else monto


def parsear_fecha(valor) -> datetime | None:
    """El portal manda '2020-12-15T15:21:38'. Vacio cuando no aplica."""
    texto = str(valor or "").strip()
    if not texto:
        return None
    try:
        return datetime.fromisoformat(texto)
    except ValueError:
        pass
    for formato in ("%d/%m/%Y %H:%M:%S", "%d/%m/%Y"):
        try:
            return datetime.strptime(texto, formato)
        except ValueError:
            continue
    return None


def _sin_acentos(texto: str) -> str:
    return (texto.upper().replace("Á", "A").replace("É", "E")
            .replace("Í", "I").replace("Ó", "O").replace("Ú", "U"))


# ---------------------------------------------------------------------------
# Llamada al script PHP
# ---------------------------------------------------------------------------

def ejecutar_script(argumentos: list[str], timeout: int | None = None) -> dict:
    """Corre consultar_portal.php y devuelve su JSON. Lanza ErrorPortal."""
    from app.core.config import settings

    script = _ruta_script()
    if not script.exists():
        raise ErrorPortal("configuracion", f"No existe el script {script}")
    if not (script.parent / "vendor" / "autoload.php").exists():
        raise ErrorPortal(
            "configuracion",
            f"Falta instalar dependencias: cd {script.parent} && composer install",
        )
    if not (settings.SAT_CER_PATH and settings.SAT_KEY_PATH and settings.SAT_KEY_PASSWORD):
        raise ErrorPortal("configuracion", "Falta la e.firma en .env (SAT_CER_PATH, SAT_KEY_PATH, SAT_KEY_PASSWORD)")

    entorno = os.environ.copy()
    entorno["SAT_CER_PATH"] = settings.SAT_CER_PATH
    entorno["SAT_KEY_PATH"] = settings.SAT_KEY_PATH
    entorno["SAT_KEY_PASSWORD"] = settings.SAT_KEY_PASSWORD
    entorno["SAT_PORTAL_SECLEVEL1"] = "1" if settings.SAT_PORTAL_SECLEVEL1 else "0"

    comando = [settings.SAT_PORTAL_PHP, str(script), *argumentos]
    try:
        proceso = subprocess.run(
            comando,
            capture_output=True,
            timeout=timeout or settings.SAT_PORTAL_TIMEOUT_SEGUNDOS,
            env=entorno,
            cwd=str(script.parent),
            check=False,
        )
    except subprocess.TimeoutExpired as error:
        raise ErrorPortal("timeout", f"El portal no respondio en {error.timeout} s") from error
    except FileNotFoundError as error:
        raise ErrorPortal("configuracion", f"No se encontro PHP en '{settings.SAT_PORTAL_PHP}'") from error

    salida = proceso.stdout.decode("utf-8", errors="replace").strip()
    try:
        datos = json.loads(salida)
    except json.JSONDecodeError as error:
        # PHP trono antes de imprimir su JSON (fatal error, extension faltante...).
        detalle = (proceso.stderr.decode("utf-8", errors="replace") or salida)[-800:]
        raise ErrorPortal(
            "interno", f"El script no devolvio JSON (codigo {proceso.returncode}): {detalle}"
        ) from error

    if not datos.get("ok"):
        raise ErrorPortal(datos.get("tipo_error") or "interno", datos.get("error") or "Error sin detalle")
    return datos


# ---------------------------------------------------------------------------
# Normalizacion
# ---------------------------------------------------------------------------

def normalizar_cfdi(fila: dict, rfc_propio: str) -> tuple[dict | None, str | None]:
    """
    Convierte una fila del portal al formato de FacturasRecibidas.
    Devuelve (registro, None) o (None, motivo_de_rechazo).
    """
    uuid = str(fila.get("uuid") or "").strip().upper()
    if not PATRON_UUID.match(uuid):
        return None, f"folio invalido {uuid!r}"

    rfc_emisor = str(fila.get("rfcEmisor") or "").strip().upper()
    rfc_receptor = str(fila.get("rfcReceptor") or "").strip().upper()
    if rfc_receptor != rfc_propio:
        # En la consulta de recibidas no deberia pasar nunca. Si pasa, el
        # portal cambio algo y es mejor enterarse por el contador.
        return None, f"{uuid}: receptor {rfc_receptor!r} no es {rfc_propio}"
    if not rfc_emisor:
        return None, f"{uuid}: sin RFC emisor"

    fecha = parsear_fecha(fila.get("fechaEmision"))
    if fecha is None:
        return None, f"{uuid}: fecha de emision ilegible {fila.get('fechaEmision')!r}"

    monto = parsear_monto(fila.get("total"))
    if monto is None:
        return None, f"{uuid}: total ilegible {fila.get('total')!r}"

    pac = str(fila.get("pacCertifico") or "").strip().upper()

    registro = {
        "folio_fiscal": uuid,
        "rfc_emisor": rfc_emisor[:13],
        "nombre_emisor": (str(fila.get("nombreEmisor") or "").strip() or None),
        "rfc_receptor": rfc_receptor,
        "nombre_receptor": (str(fila.get("nombreReceptor") or "").strip() or None),
        "fecha_emision": fecha,
        "monto_total": monto,
        "efecto_comprobante": EFECTOS.get(_sin_acentos(str(fila.get("efectoComprobante") or "").strip())),
        "rfc_pac": pac if PATRON_RFC.match(pac) else None,
        "sat_estado": ESTADOS.get(_sin_acentos(str(fila.get("estadoComprobante") or "").strip())),
        "fecha_cancelacion": parsear_fecha(fila.get("fechaDeCancelacion")),
    }
    return registro, None


def leer_xmls(carpeta: Path) -> dict[str, bytes]:
    """{UUID: bytes} de los uuid.xml que dejo el script."""
    xmls = {}
    for archivo in carpeta.glob("*.xml"):
        contenido = archivo.read_bytes()
        if contenido.strip():
            xmls[archivo.stem.strip().upper()] = contenido
    return xmls


# ---------------------------------------------------------------------------
# Escritura
# ---------------------------------------------------------------------------

def guardar_recibidas(
    db: Session,
    registros: list[dict],
    xmls: dict[str, bytes],
    vista_en: datetime,
) -> tuple[int, int]:
    """UPSERT en FacturasRecibidas. Devuelve (nuevas, actualizadas). No hace commit."""
    from app.modelos.facturas_recibidas import FacturasRecibidas

    # Un mismo UUID dos veces en la misma sentencia rompe ON CONFLICT DO UPDATE.
    unicos = {r["folio_fiscal"]: r for r in registros}
    filas = []
    for uuid, registro in unicos.items():
        fila = dict(registro)
        fila["origen"] = "portal"
        fila["id_solicitud"] = None
        fila["xml_factura"] = xmls.get(uuid)
        fila["fecha_vista_portal"] = vista_en
        filas.append(fila)

    tabla = FacturasRecibidas.__table__
    nuevas = actualizadas = 0

    for inicio in range(0, len(filas), TAMANIO_LOTE):
        lote = filas[inicio:inicio + TAMANIO_LOTE]
        sentencia = insert(tabla).values(lote)
        nuevo = sentencia.excluded

        campos = {
            # Estatus: el portal es el SAT mismo. Si trae valor, manda.
            "sat_estado": func.coalesce(nuevo.sat_estado, tabla.c.sat_estado),
            "fecha_cancelacion": func.coalesce(nuevo.fecha_cancelacion, tabla.c.fecha_cancelacion),
            # Faltantes: solo se rellenan si estan vacios.
            "efecto_comprobante": func.coalesce(tabla.c.efecto_comprobante, nuevo.efecto_comprobante),
            "rfc_pac": func.coalesce(tabla.c.rfc_pac, nuevo.rfc_pac),
            "nombre_emisor": func.coalesce(tabla.c.nombre_emisor, nuevo.nombre_emisor),
            "nombre_receptor": func.coalesce(tabla.c.nombre_receptor, nuevo.nombre_receptor),
            "xml_factura": func.coalesce(tabla.c.xml_factura, nuevo.xml_factura),
            "fecha_vista_portal": nuevo.fecha_vista_portal,
            # onupdate= del modelo no dispara en ON CONFLICT.
            "actualizado_en": func.now(),
        }
        sentencia = sentencia.on_conflict_do_update(
            index_elements=["folio_fiscal"], set_=campos,
        ).returning(literal_column("(xmax = 0)").label("es_insert"))

        for (es_insert,) in db.execute(sentencia):
            if es_insert:
                nuevas += 1
            else:
                actualizadas += 1

    return nuevas, actualizadas


def _folios_con_xml(db: Session, desde: date, hasta: date) -> list[str]:
    """Los que ya tienen XML en el rango: no hay que volver a bajarlos."""
    from app.modelos.facturas_recibidas import FacturasRecibidas

    filas = (
        db.query(FacturasRecibidas.folio_fiscal)
        .filter(FacturasRecibidas.fecha_emision >= datetime.combine(desde, datetime.min.time()))
        .filter(FacturasRecibidas.fecha_emision < datetime.combine(hasta + timedelta(days=1), datetime.min.time()))
        .filter(FacturasRecibidas.__table__.c.xml_factura.isnot(None))
        .all()
    )
    return [f.folio_fiscal for f in filas]


# ---------------------------------------------------------------------------
# Candado
# ---------------------------------------------------------------------------

class _Candado:
    """
    pg_try_advisory_lock en una conexion DEDICADA. Un candado de sesion de
    Postgres pertenece a la conexion que lo pidio; si se pidiera con la
    Session del ORM, el pool podria devolver otra conexion para soltarlo.
    """

    def __init__(self, db: Session):
        self._engine = db.get_bind()
        self._conexion = None

    def __enter__(self):
        self._conexion = self._engine.connect()
        obtenido = self._conexion.execute(
            text("SELECT pg_try_advisory_lock(:llave)"), {"llave": LLAVE_CANDADO}
        ).scalar()
        if not obtenido:
            self._conexion.close()
            raise SincronizacionEnCurso("Ya hay una sincronizacion con el portal en curso")
        return self

    def __exit__(self, *_):
        try:
            self._conexion.execute(
                text("SELECT pg_advisory_unlock(:llave)"), {"llave": LLAVE_CANDADO}
            )
            self._conexion.commit()
        finally:
            self._conexion.close()


# ---------------------------------------------------------------------------
# Orquestacion
# ---------------------------------------------------------------------------

def sincronizar_recibidas(
    db: Session,
    desde: date,
    hasta: date,
    motivo: str = MOTIVO_PROGRAMADA,
    descargar_xml: bool = True,
) -> dict:
    """
    Una corrida completa: portal -> FacturasRecibidas, con su fila en
    SincronizacionesPortal. Hace commit. Nunca lanza ErrorPortal: el fallo
    queda registrado en la bitacora y en el resumen devuelto.
    Lanza SincronizacionEnCurso si ya hay otra corrida.
    """
    from app.core.config import settings
    from app.modelos.sincronizacion_portal import SincronizacionesPortal

    hoy = hoy_mexico()
    if hasta > hoy:
        hasta = hoy
    if desde > hasta:
        raise ValueError(f"desde {desde} es posterior a hasta {hasta}")
    if (hasta - desde).days + 1 > DIAS_MAXIMOS_POR_CORRIDA:
        raise ValueError(f"Rango de mas de {DIAS_MAXIMOS_POR_CORRIDA} dias: partelo en varias corridas")

    with _Candado(db):
        bitacora = SincronizacionesPortal(
            tipo_comprobante=TIPO_RECIBIDOS,
            fecha_desde=desde,
            fecha_hasta=hasta,
            motivo=motivo,
            estado=EN_CURSO,
            inicio=_ahora(),
        )
        db.add(bitacora)
        db.commit()

        carpeta = Path(tempfile.mkdtemp(prefix="portal_sat_"))
        try:
            argumentos = ["--tipo", TIPO_RECIBIDOS, "--desde", desde.isoformat(), "--hasta", hasta.isoformat()]
            if descargar_xml:
                omitir = carpeta / "omitir.txt"
                omitir.write_text("\n".join(_folios_con_xml(db, desde, hasta)), encoding="utf-8")
                argumentos += ["--dir", str(carpeta / "xml"), "--omitir", str(omitir)]
            else:
                argumentos.append("--sin-xml")

            datos = ejecutar_script(argumentos)

            registros, rechazos = [], []
            for fila in datos.get("cfdis", []):
                registro, motivo_rechazo = normalizar_cfdi(fila, settings.RFC_EMPRESA.upper())
                if registro:
                    registros.append(registro)
                else:
                    rechazos.append(motivo_rechazo)

            xmls = leer_xmls(carpeta / "xml") if descargar_xml else {}
            aceptados = {r["folio_fiscal"] for r in registros}
            xmls = {uuid: xml for uuid, xml in xmls.items() if uuid in aceptados}
            nuevas, actualizadas = guardar_recibidas(db, registros, xmls, _ahora())

            avisos = list(datos.get("avisos") or [])
            if datos.get("error_descarga"):
                avisos.append(f"Descarga de XML incompleta: {datos['error_descarga']}")
            if rechazos:
                avisos.append(f"{len(rechazos)} fila(s) rechazada(s). Ej: " + " | ".join(rechazos[:5]))
                logger.warning("Portal SAT: %d fila(s) rechazada(s): %s", len(rechazos), rechazos[:5])

            bitacora.estado = EXITOSA
            bitacora.cfdis_encontrados = int(datos.get("total") or 0)
            bitacora.nuevas = nuevas
            bitacora.actualizadas = actualizadas
            bitacora.xml_descargados = len(xmls)
            bitacora.rechazadas = len(rechazos)
            bitacora.avisos = "\n".join(avisos) or None
            bitacora.fin = _ahora()
            db.commit()

            logger.info(
                "Portal SAT %s a %s (%s): %d en el portal, %d nuevas, %d actualizadas, %d XML",
                desde, hasta, motivo, bitacora.cfdis_encontrados, nuevas, actualizadas, len(xmls),
            )

        except ErrorPortal as error:
            db.rollback()
            bitacora.estado = FALLIDA
            bitacora.tipo_error = error.tipo
            bitacora.error = error.mensaje[:4000]
            bitacora.fin = _ahora()
            db.add(bitacora)
            db.commit()
            logger.error("Portal SAT fallo (%s): %s", error.tipo, error.mensaje)

        except Exception as error:  # noqa: BLE001
            db.rollback()
            bitacora.estado = FALLIDA
            bitacora.tipo_error = "interno"
            bitacora.error = f"{type(error).__name__}: {error}"[:4000]
            bitacora.fin = _ahora()
            db.add(bitacora)
            db.commit()
            logger.exception("Portal SAT: error interno")

        finally:
            shutil.rmtree(carpeta, ignore_errors=True)

        return resumen_corrida(bitacora)


def resumen_corrida(b) -> dict:
    return {
        "id": b.id,
        "estado": b.estado,
        "motivo": b.motivo,
        "fecha_desde": b.fecha_desde,
        "fecha_hasta": b.fecha_hasta,
        "inicio": b.inicio,
        "fin": b.fin,
        "cfdis_encontrados": b.cfdis_encontrados,
        "nuevas": b.nuevas,
        "actualizadas": b.actualizadas,
        "xml_descargados": b.xml_descargados,
        "rechazadas": b.rechazadas,
        "tipo_error": b.tipo_error,
        "error": b.error,
        "avisos": b.avisos,
    }


def estado_sincronizacion(db: Session) -> dict:
    """Lo que el frontend pinta arriba de la tabla de Recibidas."""
    from app.core.config import settings
    from app.modelos.facturas_recibidas import FacturasRecibidas
    from app.modelos.sincronizacion_portal import SincronizacionesPortal

    ultima = db.query(SincronizacionesPortal).order_by(SincronizacionesPortal.inicio.desc()).first()
    ultima_exitosa = (
        db.query(SincronizacionesPortal)
        .filter(SincronizacionesPortal.estado == EXITOSA)
        .order_by(SincronizacionesPortal.fin.desc())
        .first()
    )
    ahora = _ahora()
    horas_sin_exito = (
        round((ahora - ultima_exitosa.fin).total_seconds() / 3600, 1)
        if ultima_exitosa and ultima_exitosa.fin else None
    )
    nuevas_24h = (
        db.query(func.count(FacturasRecibidas.id_factura_recibida))
        .filter(FacturasRecibidas.creado_en >= ahora - timedelta(hours=24))
        .scalar()
    )

    alerta = None
    if settings.SAT_PORTAL_ACTIVO:
        if ultima_exitosa is None:
            alerta = "Todavia no hay una sincronizacion exitosa con el portal del SAT."
        elif horas_sin_exito is not None and horas_sin_exito >= HORAS_ALERTA:
            alerta = f"No se ha podido consultar el SAT desde hace {horas_sin_exito:.0f} horas."

    return {
        "activo": settings.SAT_PORTAL_ACTIVO,
        "en_curso": bool(ultima and ultima.estado == EN_CURSO),
        "ultima_sincronizacion_exitosa": ultima_exitosa.fin if ultima_exitosa else None,
        "horas_sin_sincronizar": horas_sin_exito,
        "nuevas_ultimas_24h": nuevas_24h or 0,
        "alerta": alerta,
        "ultima_corrida": resumen_corrida(ultima) if ultima else None,
    }


def limpiar_corridas_colgadas(db: Session, minutos: int = 60) -> int:
    """
    Si el servicio se reinicio a media corrida, su fila queda EN_CURSO para
    siempre y el frontend diria "en curso" sin fin. Se cierran aqui.
    """
    from app.modelos.sincronizacion_portal import SincronizacionesPortal

    limite = _ahora() - timedelta(minutes=minutos)
    colgadas = (
        db.query(SincronizacionesPortal)
        .filter(SincronizacionesPortal.estado == EN_CURSO)
        .filter(SincronizacionesPortal.inicio < limite)
        .all()
    )
    for c in colgadas:
        c.estado = FALLIDA
        c.tipo_error = "interno"
        c.error = "La corrida no termino (reinicio del servicio o proceso interrumpido)"
        c.fin = _ahora()
    if colgadas:
        db.commit()
    return len(colgadas)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _probar(db: Session, desde: date, hasta: date, tipo: str) -> None:
    """Consulta el portal y compara contra la base. No escribe nada."""
    from app.modelos.facturas_recibidas import FacturasRecibidas

    datos = ejecutar_script(
        ["--tipo", tipo, "--desde", desde.isoformat(), "--hasta", hasta.isoformat(), "--sin-xml"]
    )
    cfdis = datos.get("cfdis", [])
    print(f"\nPortal: {len(cfdis)} CFDI {tipo} del {desde} al {hasta} (RFC {datos.get('rfc')})")
    for aviso in datos.get("avisos") or []:
        print(f"  AVISO: {aviso}")

    for fila in cfdis[:15]:
        print(f"  {fila.get('uuid', '')[:8]}  {fila.get('fechaEmision', '')[:10]}  "
              f"{fila.get('rfcEmisor', ''):<14}{(fila.get('nombreEmisor') or '')[:30]:<31}"
              f"{fila.get('total', ''):>14}  {fila.get('efectoComprobante', ''):<8} {fila.get('estadoComprobante', '')}")
    if len(cfdis) > 15:
        print(f"  ... y {len(cfdis) - 15} mas")

    if tipo != TIPO_RECIBIDOS:
        return

    en_portal = {str(f.get("uuid", "")).upper() for f in cfdis}
    en_base = {
        f.folio_fiscal: f.origen
        for f in db.query(FacturasRecibidas.folio_fiscal, FacturasRecibidas.origen)
        .filter(FacturasRecibidas.fecha_emision >= datetime.combine(desde, datetime.min.time()))
        .filter(FacturasRecibidas.fecha_emision < datetime.combine(hasta + timedelta(days=1), datetime.min.time()))
        .all()
    }
    solo_portal = en_portal - set(en_base)
    solo_base = set(en_base) - en_portal
    print("\nComparacion contra FacturasRecibidas en el mismo rango:")
    print(f"  En los dos:            {len(en_portal & set(en_base))}")
    print(f"  Solo en el portal:     {len(solo_portal)}  (se agregarian)")
    print(f"  Solo en la base:       {len(solo_base)}")
    for uuid in sorted(solo_base)[:10]:
        print(f"    {uuid}  origen={en_base[uuid]}")
    print("\nNo se escribio nada. Para guardar: --desde ... --hasta ... --apply")


def main() -> None:
    import argparse

    logging.basicConfig(level=logging.INFO, format="%(levelname)-8s %(message)s")

    parser = argparse.ArgumentParser(description="Sincroniza recibidas con el portal del SAT.")
    parser.add_argument("--verificar-sesion", action="store_true", help="Solo prueba el login con la e.firma")
    parser.add_argument("--probar", action="store_true", help="Consulta y compara, sin escribir")
    parser.add_argument("--estado", action="store_true", help="Muestra el estado de la sincronizacion")
    parser.add_argument("--desde", type=date.fromisoformat)
    parser.add_argument("--hasta", type=date.fromisoformat)
    parser.add_argument("--tipo", default=TIPO_RECIBIDOS, choices=[TIPO_RECIBIDOS, TIPO_EMITIDOS])
    parser.add_argument("--sin-xml", action="store_true", help="Solo metadata")
    parser.add_argument("--apply", action="store_true", dest="aplicar", help="Guardar en la base")
    args = parser.parse_args()

    from app.modelos import (  # noqa: F401  registra los mapeos
        usuario, factura, estados, configuracion, conceptos,
        complemento_pago, orden_compra, cp_documento_relacionado,
        correo_procesado, cliente, solicitud_sat, facturas_recibidas,
        sincronizacion_portal,
    )
    from app.BaseDeDatos import SessionLocal

    if args.verificar_sesion:
        datos = ejecutar_script(["--verificar-sesion"], timeout=120)
        print(f"OK: sesion iniciada como {datos.get('rfc')}. e.firma vence: {datos.get('certificado_vence')}")
        return

    db = SessionLocal()
    try:
        if args.estado:
            print(json.dumps(estado_sincronizacion(db), default=str, indent=2, ensure_ascii=False))
            return

        if not (args.desde and args.hasta):
            parser.error("--desde y --hasta son obligatorios")

        if args.probar or not args.aplicar:
            _probar(db, args.desde, args.hasta, args.tipo)
            return

        if args.tipo != TIPO_RECIBIDOS:
            parser.error("Por ahora solo se guardan recibidas; usa --probar para emitidas")

        resumen = sincronizar_recibidas(
            db, args.desde, args.hasta, MOTIVO_CLI, descargar_xml=not args.sin_xml
        )
        print(json.dumps(resumen, default=str, indent=2, ensure_ascii=False))
    except ErrorPortal as error:
        print(f"ERROR ({error.tipo}): {error.mensaje}")
        raise SystemExit(1)
    finally:
        db.close()


if __name__ == "__main__":
    main()
