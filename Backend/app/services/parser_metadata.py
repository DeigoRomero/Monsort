"""
Parser del TXT de Metadata que devuelve la Descarga Masiva del SAT.

Funcion pura: recibe bytes, devuelve dicts. No toca la base de datos ni
la red, asi que se puede probar con un ZIP de ejemplo en segundos.

El parseo es POR NOMBRE DE ENCABEZADO, nunca por posicion de columna:
si el SAT reordena o agrega campos, el parser no se entera. Si falta un
encabezado esperado, truena fuerte en vez de devolver None silencioso.
"""
import io
import logging
import zipfile
from datetime import datetime
from decimal import Decimal, InvalidOperation

logger = logging.getLogger(__name__)


# RFC del cliente. Toda fila donde Monsort no aparezca en el papel esperado
# se rechaza: significa que la solicitud se armo mal, y es preferible
# enterarse por el contador de rechazos que por una factura ajena en el
# dashboard.
#
# El mismo TXT sirve para las dos direcciones, solo cambia en que columna
# debe estar el RFC de Monsort:
#
#   rol="receptor"  recibidas  -> un proveedor le emitio a Monsort
#   rol="emisor"    emitidas   -> Monsort le emitio a un cliente
#
# Por eso se parametriza en vez de duplicar el parser: el layout, el
# separador, el BOM y el mapeo de estatus son identicos.
RFC_RECEPTOR_ESPERADO = "MSF140227BF7"   # se conserva por compatibilidad

ROL_RECEPTOR = "receptor"
ROL_EMISOR = "emisor"

COLUMNA_POR_ROL = {
    ROL_RECEPTOR: "rfc_receptor",
    ROL_EMISOR: "rfc_emisor",
}

# Encabezado del SAT -> nombre de columna en FacturasRecibidas.
#
# PENDIENTE DE VERIFICAR contra el primer ZIP real. Los nombres de abajo
# son los del layout documentado, pero conviene confirmarlos: si alguno
# no coincide, _mapear_encabezados() lanza ValueError con el nombre exacto
# que falto y la lista de los que si venian.
MAPEO = {
    "Uuid": "folio_fiscal",
    "RfcEmisor": "rfc_emisor",
    "NombreEmisor": "nombre_emisor",
    "RfcReceptor": "rfc_receptor",
    "NombreReceptor": "nombre_receptor",
    "RfcPac": "rfc_pac",
    "FechaEmision": "fecha_emision",
    "Monto": "monto_total",
    "EfectoComprobante": "efecto_comprobante",
    "Estatus": "sat_estado",
    "FechaCancelacion": "fecha_cancelacion",
}

# Columnas sin las cuales la fila no sirve para nada.
OBLIGATORIAS = ("Uuid", "RfcEmisor", "RfcReceptor", "FechaEmision", "Monto", "Estatus")

# Los mismos literales que escribe la verificacion SOAP en Facturas.sat_estado.
# Si aqui se escribiera "VIGENTE" y alla "Vigente", el filtro del dashboard
# partiria los resultados en dos grupos invisibles.
MAPEO_ESTATUS = {
    "1": "Vigente",
    "0": "Cancelado",
}

# El separador documentado es la tilde, pero se detecta en runtime por si
# el servicio cambia. El orden importa: se prueba de mas raro a mas comun.
SEPARADORES_CANDIDATOS = ("~", "|", "\t", ",")


def _decodificar(datos: bytes) -> str:
    """
    utf-8-sig y no utf-8: si el TXT trae BOM, el primer encabezado se
    llamaria "\ufeffUuid" y el lookup fallaria con un nombre que a simple
    vista se ve identico al correcto.
    """
    for codificacion in ("utf-8-sig", "latin-1"):
        try:
            return datos.decode(codificacion)
        except UnicodeDecodeError:
            continue
    raise ValueError("No se pudo decodificar el TXT de metadata")


def _detectar_separador(linea_encabezado: str) -> str:
    """Elige el separador que produce mas columnas."""
    mejor = max(SEPARADORES_CANDIDATOS, key=lambda s: linea_encabezado.count(s))
    if linea_encabezado.count(mejor) == 0:
        raise ValueError(
            f"No se reconocio el separador del TXT. Encabezado: {linea_encabezado[:200]!r}"
        )
    return mejor


def _mapear_encabezados(linea_encabezado: str, separador: str) -> dict[str, int]:
    """Devuelve {nombre_encabezado: indice}. Truena si falta alguno obligatorio."""
    columnas = [c.strip() for c in linea_encabezado.split(separador)]
    indices = {nombre: i for i, nombre in enumerate(columnas)}

    faltantes = [n for n in OBLIGATORIAS if n not in indices]
    if faltantes:
        raise ValueError(
            f"El TXT de metadata no trae las columnas {faltantes}. "
            f"Encabezados encontrados: {columnas}"
        )

    no_mapeados = [n for n in MAPEO if n not in indices]
    if no_mapeados:
        # No es fatal: son opcionales. Pero conviene saberlo.
        logger.warning(
            "Columnas esperadas ausentes en el TXT (se llenaran con None): %s",
            no_mapeados,
        )

    return indices


def _a_decimal(valor: str | None):
    """Decimal y nunca float: float reintroduce el error binario que
    evita DECIMAL(18,6) en la tabla."""
    if not valor or not valor.strip():
        return None
    try:
        return Decimal(valor.strip())
    except InvalidOperation:
        return None


def _a_fecha(valor: str | None):
    """El SAT manda 'YYYY-MM-DDTHH:MM:SS'. Vacio en las vigentes."""
    if not valor or not valor.strip():
        return None
    try:
        return datetime.fromisoformat(valor.strip())
    except ValueError:
        return None


def _parsear_linea(linea: str, separador: str, indices: dict[str, int]) -> dict:
    """Convierte una linea cruda en un dict con los nombres de columna."""
    campos = linea.split(separador)

    def leer(encabezado: str):
        i = indices.get(encabezado)
        if i is None or i >= len(campos):
            return None
        valor = campos[i].strip()
        return valor if valor else None

    return {
        "folio_fiscal": leer("Uuid"),
        "rfc_emisor": (leer("RfcEmisor") or "").upper() or None,
        "nombre_emisor": leer("NombreEmisor"),
        "rfc_receptor": (leer("RfcReceptor") or "").upper() or None,
        "nombre_receptor": leer("NombreReceptor"),
        "rfc_pac": leer("RfcPac"),
        "fecha_emision": _a_fecha(leer("FechaEmision")),
        "monto_total": _a_decimal(leer("Monto")),
        "efecto_comprobante": leer("EfectoComprobante"),
        "sat_estado": MAPEO_ESTATUS.get(leer("Estatus") or ""),
        "fecha_cancelacion": _a_fecha(leer("FechaCancelacion")),
        "_estatus_crudo": leer("Estatus"),
    }


def _validar(fila: dict, rol: str = ROL_RECEPTOR, rfc_propio: str | None = None) -> str | None:
    """Devuelve el motivo de rechazo, o None si la fila es valida."""
    rfc_propio = (rfc_propio or RFC_RECEPTOR_ESPERADO).upper()
    columna_rfc = COLUMNA_POR_ROL[rol]

    if not fila["folio_fiscal"]:
        return "Sin folio fiscal"

    if fila["monto_total"] is None:
        return "Monto ausente o no numerico"

    if fila["fecha_emision"] is None:
        return "Fecha de emision ausente o con formato desconocido"

    if fila["sat_estado"] is None:
        # No se asume vigente: el default optimista dejaria una cancelada
        # disfrazada de buena en la vista del cliente.
        return f"Estatus desconocido: {fila['_estatus_crudo']!r}"

    if fila[columna_rfc] != rfc_propio:
        return (
            f"{rol.capitalize()} inesperado {fila[columna_rfc]!r} "
            f"(se esperaba {rfc_propio})"
        )

    return None


def parsear_metadata(
    contenido_zip: bytes,
    rol: str = ROL_RECEPTOR,
    rfc_propio: str | None = None,
) -> tuple[list[dict], list[dict]]:
    """
    Devuelve (filas_validas, filas_rechazadas).

    filas_validas:     dicts listos para la ingesta, con los nombres de
                       columna de FacturasRecibidas / MetadataEmitidas
                       (comparten los nombres a proposito).
    filas_rechazadas:  [{'linea': str, 'motivo': str, 'archivo': str}, ...]
                       para alimentar SolicitudesSAT.error_ingesta.

    rol:         "receptor" para recibidas (Monsort recibe), "emisor" para
                 emitidas (Monsort emite). Decide en que columna se exige el
                 RFC de Monsort. El default conserva el comportamiento previo.
    rfc_propio:  el RFC de Monsort. Si no se pasa, usa la constante del
                 modulo, para que el parser siga siendo probable sin cargar
                 la configuracion.

    No toca la base de datos.
    """
    if rol not in COLUMNA_POR_ROL:
        raise ValueError(f"rol invalido: {rol!r}. Use {list(COLUMNA_POR_ROL)}")

    validas: list[dict] = []
    rechazadas: list[dict] = []

    with zipfile.ZipFile(io.BytesIO(contenido_zip)) as zf:
        nombres_txt = [n for n in zf.namelist() if n.lower().endswith(".txt")]

        if not nombres_txt:
            raise ValueError(
                f"El ZIP no contiene ningun .txt. Miembros: {zf.namelist()}"
            )

        # Un paquete puede traer varios TXT, cada uno con su propio
        # renglon de encabezado. No asumir que solo hay uno.
        for nombre in nombres_txt:
            texto = _decodificar(zf.read(nombre))
            lineas = [l for l in texto.splitlines() if l.strip()]

            if not lineas:
                logger.warning("El archivo %s venia vacio", nombre)
                continue

            separador = _detectar_separador(lineas[0])
            indices = _mapear_encabezados(lineas[0], separador)

            for linea in lineas[1:]:
                try:
                    fila = _parsear_linea(linea, separador, indices)
                except Exception as e:
                    rechazadas.append({
                        "linea": linea[:300],
                        "motivo": f"Error al parsear: {e}",
                        "archivo": nombre,
                    })
                    continue

                motivo = _validar(fila, rol, rfc_propio)
                if motivo:
                    rechazadas.append({
                        "linea": linea[:300],
                        "motivo": motivo,
                        "archivo": nombre,
                    })
                    continue

                fila.pop("_estatus_crudo", None)
                validas.append(fila)

    logger.info(
        "Metadata parseada (%s): %d validas, %d rechazadas",
        rol, len(validas), len(rechazadas),
    )
    return validas, rechazadas