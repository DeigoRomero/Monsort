"""
Detector de números de orden de compra (OC / PO).

Por qué existe
--------------
El detector anterior era una lista de regex que se quedaba con el PRIMER
número de 4+ dígitos cuando ninguna etiqueta funcionaba. En los PDFs de
Hyson la etiqueta "Purchase Order" está en un renglón y el número en el de
abajo, así que caía en el respaldo y devolvía "2026" (el año de la fecha).
Con "OC_GRUPOMONSORT_CL-0039.pdf" devolvía "0039", y las OCs que llegan en el
CUERPO del correo (Regal Rexnord / SciQuest) ni siquiera se veían, porque
solo se leían adjuntos.

Cómo decide ahora
-----------------
Junta candidatos de TODAS las fuentes y los califica:

  fuente                               puntos base
  ─────────────────────────────────    ───────────
  PDF, etiqueta fuerte en el renglón        100   "PO NUMBER C-10003761667"
  PDF, valor debajo de la etiqueta           95   Hyson: "Purchase Order" / "NHY13E2871"
  Asunto con etiqueta                        90   "Regal Rexnord Procurement, PO#: D1009036"
  Cuerpo del correo con etiqueta             85
  Nombre de archivo con prefijo PO/OC        80   "PO-NHY13E2871.pdf"
  Nombre de archivo sin prefijo              60   "63600796.pdf"
  Número suelto sin etiqueta                 10   (último recurso)

  +25 por cada fuente DISTINTA que coincide en el mismo número.

Y descarta lo que parece número pero no es OC: fechas, años sueltos, RFC,
teléfonos, códigos postales, montos, números de proveedor, UUID.

Además clasifica si un PDF ES una orden de compra: una cotización que dice
"favor de enviar su PO" ya no se guarda como OC.

Todo es texto: no hay acceso a BD aquí, para poder probarlo aislado.
"""
from __future__ import annotations

import html
import re
import unicodedata
from dataclasses import dataclass, field
from html.parser import HTMLParser

# ═══════════════════════════════════════════════════════════════════════════
# NORMALIZACIÓN Y VALIDACIÓN DE TOKENS
# ═══════════════════════════════════════════════════════════════════════════

_RE_FECHA = re.compile(
    r"^(\d{1,2}[/.\-]\d{1,2}[/.\-]\d{2,4}|\d{4}[/.\-]\d{1,2}[/.\-]\d{1,2}"
    r"|(?:19|20)\d\d(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])(?:T\d*)?)$"
)
_RE_ANIO = re.compile(r"^(19[89]\d|20\d\d)$")
_RE_RFC = re.compile(r"^[A-Z&Ñ]{3,4}-?\d{6}-?[A-Z0-9]{3}$")
_RE_MONTO = re.compile(r"^\$?\d{1,3}(,\d{3})+(\.\d+)?$|^\$?\d+\.\d{1,2}$")
_RE_TELEFONO = re.compile(r"^\(?\d{3}\)?[\-\s]?\d{3}[\-\s]\d{4}$")
_RE_UUID = re.compile(r"^[0-9A-F]{8}-[0-9A-F]{4}-", re.I)
_RE_HORA = re.compile(r"^\d{1,2}:\d{2}")
_RE_COPIA = re.compile(r"\s*\(\d+\)")           # "archivo (1) (1).pdf"
_RE_VERSION = re.compile(r"^V\d{1,2}$")          # "_v1_"

# Prefijos que son ETIQUETA, no parte del número: "PO-NHY13E2871" -> "NHY13E2871".
# Solo con separador explícito: "PO26004288" (Samsung) se queda entero.
_RE_PREFIJO_ETIQUETA = re.compile(r"^(?:P\.?O\.?|O\.?C\.?)[\-_.#:]+(?=[A-Z0-9])", re.I)

# RFC de Monsort y otros valores propios que jamás son la OC de un cliente.
VALORES_PROPIOS = {"MSF140227BF7"}


def limpiar_token(token: str) -> str:
    """Quita la puntuación que rodea al número: '#CL-0308.' -> 'CL-0308'."""
    t = (token or "").strip().upper()
    t = t.strip("#:;,.()[]{}\"'«»“”*|")
    t = t.replace("º", "").replace("°", "")
    return t


def contar_digitos(t: str) -> int:
    return sum(c.isdigit() for c in t)


def es_token_valido(token: str) -> bool:
    """¿Puede ser un número de OC? Estructura solamente, sin contexto."""
    t = limpiar_token(token)
    if not (3 <= len(t) <= 30):
        return False
    if contar_digitos(t) < 3:
        return False
    if not re.fullmatch(r"[A-Z0-9][A-Z0-9\-/.]*[A-Z0-9]", t):
        return False
    if t in VALORES_PROPIOS:
        return False
    if (_RE_FECHA.match(t) or _RE_ANIO.match(t) or _RE_RFC.match(t)
            or _RE_MONTO.match(t) or _RE_TELEFONO.match(t)
            or _RE_UUID.match(t) or _RE_HORA.match(t)):
        return False
    # Fechas con mes en letras dentro del token: "SEP/08/2026", "2026-SEPT"
    if re.search(r"(ENE|FEB|MAR|ABR|MAY|JUN|JUL|AGO|SEP|OCT|NOV|DIC|JAN|APR|AUG|DEC)[/\-]\d", t) \
            and re.search(r"\d{4}$", t):
        return False
    return True


def quitar_prefijo_etiqueta(t: str) -> str:
    t = limpiar_token(t)
    sin = _RE_PREFIJO_ETIQUETA.sub("", t)
    return sin if sin != t and es_token_valido(sin) else t


def clave_oc(valor: str | None) -> str | None:
    """
    Llave de comparación entre OCs y facturas.

    "#PO-VSM-12062", "PO-VSM-12062" y "VSM-12062" dan lo mismo; también
    "BOS-507-91753." y "BOS-507-91753", o "40083-00" y "40083".
    Solo para COMPARAR: lo que se muestra al usuario es el valor original.
    """
    if not valor:
        return None
    t = limpiar_token(valor.split("\n")[0])
    t = re.sub(r"\s+", "", t)
    t = re.sub(r"^(?:P\.?O\.?|O\.?C\.?)[\-_.#:]*(?=[A-Z0-9]*\d)", "", t)
    t = re.sub(r"-0{1,3}$", "", t)          # sufijo de revisión: 40083-00
    t = re.sub(r"[^A-Z0-9]", "", t)
    return t or None


def nucleo_numerico(valor: str | None) -> str | None:
    """La corrida de dígitos más larga (>= 4): 'C-10003761667' -> '10003761667'."""
    if not valor:
        return None
    corridas = re.findall(r"\d+", valor)
    if not corridas:
        return None
    mayor = max(corridas, key=len)
    return mayor if len(mayor) >= 4 else None


# ═══════════════════════════════════════════════════════════════════════════
# CANDIDATOS
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class Candidato:
    valor: str
    puntos: int
    fuente: str          # pdf | pdf_layout | asunto | cuerpo | archivo | suelto
    detalle: str = ""


@dataclass
class ResultadoOC:
    numero: str | None
    confianza: str                     # alta | media | baja | ninguna
    puntos: int = 0
    fuentes: list[str] = field(default_factory=list)
    candidatos: list[Candidato] = field(default_factory=list)

    def __bool__(self) -> bool:
        return bool(self.numero)


# Etiquetas que anuncian el número. Orden = prioridad dentro del mismo renglón.
# Se aplican sobre texto con acentos quitados y en mayúsculas.
_ETIQUETAS_FUERTES = [
    r"PO\s*/\s*REFERENCE\s*NO\.?",
    r"PURCHASE\s+ORDER\s*(?:NUMBER|NO\.?|NUM\.?|#)",
    r"P\.?\s?O\.?\s*(?:NUMBER|NUM(?:ERO)?\.?|NO\.?|#)(?:\s*/\s*DATE)?",
    r"(?:NUMERO|NO\.?|NUM\.?)\s*(?:DE\s+)?(?:LA\s+)?ORDEN\s+DE\s+COMPRA",
    r"ORDEN\s+DE\s+COMPRA\s*(?:NO\.?|NUM(?:ERO)?\.?|#)",
    r"(?:NUMERO|NO\.?|NUM\.?)\s*(?:DE\s+)?(?:O\.?C\.?|P\.?O\.?)(?![A-Z])",
    r"(?<![A-Z])O\.?C\.?\s*(?:NO\.?|#|NUM\.?)",
    r"PEDIDO\s*(?:NO\.?|NUM(?:ERO)?\.?|#)",
]
_ETIQUETAS_MEDIAS = [
    r"ORDEN\s+DE\s+COMPRA",
    r"PURCHASE\s+ORDER",
    r"(?<![A-Z0-9])P\.?O\.?(?=\s*[:#])",
    r"(?<![A-Z0-9])O\.?C\.?(?=\s*[:#.])",
    r"(?:^|\n)\s*COMPRA\s*(?=[:#])",       # "No. Orden de\nCompra: 934541"
    r"(?<![A-Z])PEDIDO(?=\s)",
    # "PO 4504027988", "OC 732274" (sin dos puntos). "PO BOX" es otra cosa.
    r"(?<![A-Z0-9.])(?:P\.?O\.?|O\.?C\.?)(?=\s+(?!BOX\b)[A-Z0-9#]*\d)",
]
_RE_FUERTES = [re.compile(p) for p in _ETIQUETAS_FUERTES]
_RE_MEDIAS = [re.compile(p) for p in _ETIQUETAS_MEDIAS]

# Si el token viene justo después de alguna de estas, NO es la OC.
_RE_CONTEXTO_MALO = re.compile(
    r"(?<![A-Z])(VENDOR|PROVEEDOR|CUENTA|ACCOUNT|TEL[EÉ]?F?O?N?O?|PHONE|FAX|CEL|C\.?\s?P\.?|ZIP|"
    r"RFC|FOLIO\s+FISCAL|UUID|PAGINA|PAGE|CANTIDAD|QTY|QUANTITY|ITEM|REV(?:ISION)?|"
    r"VERSION|FECHA|DATE|INVOICE|FACTURA|COTIZACI[OÓ]N|QUOTE|RECEIPT|RECIBO|"
    r"CUSTOMER|CLIENTE\s+NO|SUCURSAL|PLANTA|NO\.\s*DE\s+SERIE|SERIE|MODEL|MODELO|"
    r"CATALOG|PARTE|PART\s*(?:NO|#)|SKU|BC|BCN|B\.C\.|MEXICO|TIJUANA|CALLE|AVE?\.?|"
    r"NO\.?\s*EXT|INT\.?|#\s*INT|OFNA|SUITE|MASTER)\s*[:#.]?\s*$"
)


def _sin_acentos(texto: str) -> str:
    n = unicodedata.normalize("NFKD", texto)
    return "".join(c for c in n if not unicodedata.combining(c)).upper()


def _tokens_despues(texto: str, inicio: int, max_chars: int = 60) -> list[tuple[str, int]]:
    """Tokens (con su posición) desde 'inicio', hasta fin de renglón + 1 renglón."""
    fragmento = texto[inicio:inicio + max_chars]
    # Separadores tras la etiqueta: ':', '#', '.', '/', guiones sueltos.
    salida = []
    for m in re.finditer(r"[^\s|]+", fragmento):
        salida.append((m.group(0), inicio + m.start()))
    return salida


def _candidatos_etiquetados(texto: str, fuente: str, base_fuerte: int,
                            base_media: int) -> list[Candidato]:
    """Busca 'ETIQUETA  valor' en el texto. Mira hasta 2 tokens adelante."""
    if not texto:
        return []
    t = _sin_acentos(texto)
    encontrados: list[Candidato] = []
    for lista, base in ((_RE_FUERTES, base_fuerte), (_RE_MEDIAS, base_media)):
        for patron in lista:
            for m in patron.finditer(t):
                tokens = _tokens_despues(t, m.end())
                saltos = 0
                for tok, pos in tokens[:4]:
                    limpio = limpiar_token(tok)
                    if not limpio or limpio in {"/", "-", "#", ":"}:
                        continue
                    if es_token_valido(limpio):
                        # Recuperar el valor tal cual (sin acentos no afecta a
                        # dígitos ni letras A-Z de un número de OC).
                        antes = t[max(0, pos - 30):pos]
                        if _RE_CONTEXTO_MALO.search(antes):
                            break
                        encontrados.append(Candidato(
                            quitar_prefijo_etiqueta(limpio), base, fuente,
                            f"etiqueta '{m.group(0).strip()}'",
                        ))
                        break
                    saltos += 1
                    # Palabras de relleno permitidas entre etiqueta y valor
                    if saltos > 2 or not re.fullmatch(
                        r"(NO\.?|NUM\.?|NUMBER|NUMERO|#|DATE|FECHA|/|:|DE|OC|PO|ORDER|NRO\.?)",
                        limpio,
                    ):
                        break
    return encontrados


def _candidatos_layout(palabras: list[dict] | None) -> list[Candidato]:
    """
    Valor DEBAJO de la etiqueta (encabezados en tabla).

    Hyson:   AVE NORUEGA EDIFICIO D2   Purchase Order   Date        Revision
             PARQUE INDUSTRIAL...      NHY13E2871       01/13/2026  1

    pdfplumber aplana eso a "...D2 Purchase Order Date Revision Page\\n
    PARQUE INDUSTRIAL LA MESA NHY13E2871 01/13/2026 1" y la etiqueta queda
    lejos de su valor. Con las coordenadas se ve que están en la misma columna.
    """
    if not palabras:
        return []
    ps = [dict(p, T=_sin_acentos(p.get("text", ""))) for p in palabras]
    etiquetas = [
        (("PURCHASE", "ORDER"), 95), (("ORDEN", "DE", "COMPRA"), 95),
        (("PO", "NUMBER"), 95), (("P.O.", "NUMBER"), 95), (("PO", "NO."), 95),
        (("PO/REFERENCE",), 95), (("NUMERO", "ORDEN", "DE", "COMPRA:"), 95),
        (("NUMERO", "DE", "OC"), 95), (("PEDIDO",), 70), (("OC",), 70), (("PO",), 70),
    ]
    salida: list[Candidato] = []
    n = len(ps)
    for i in range(n):
        for secuencia, base in etiquetas:
            k = len(secuencia)
            if i + k > n:
                continue
            if not all(ps[i + j]["T"].rstrip(":#") == s.rstrip(":#") for j, s in enumerate(secuencia)):
                continue
            # Deben estar en el mismo renglón
            if abs(ps[i]["top"] - ps[i + k - 1]["top"]) > 3:
                continue
            x0 = ps[i]["x0"] - 12
            x1 = ps[i + k - 1]["x1"] + 12
            abajo = ps[i]["bottom"]
            alto = max(ps[i]["bottom"] - ps[i]["top"], 6)
            # Siguiente palabra en el renglón (etiqueta: valor)
            candidatos_linea = [
                p for p in ps
                if abs(p["top"] - ps[i]["top"]) <= 3 and 0 <= p["x0"] - ps[i + k - 1]["x1"] <= 60
            ]
            candidatos_abajo = [
                p for p in ps
                if abajo - 1 <= p["top"] <= abajo + 3.2 * alto
                and p["x1"] >= x0 and p["x0"] <= x1
            ]
            candidatos_abajo.sort(key=lambda p: (p["top"], abs(p["x0"] - ps[i]["x0"])))
            for grupo, pts in ((candidatos_linea, base), (candidatos_abajo, base)):
                for p in grupo[:3]:
                    limpio = limpiar_token(p["T"])
                    if es_token_valido(limpio):
                        salida.append(Candidato(
                            quitar_prefijo_etiqueta(limpio), pts, "pdf_layout",
                            f"columna de '{' '.join(secuencia)}'",
                        ))
                        break
    return salida


_RE_ASUNTO_RESPUESTA = re.compile(r"^\s*(RE|RES|AW|R)\s*:", re.I)


def _candidatos_archivo(nombre: str | None) -> list[Candidato]:
    if not nombre:
        return []
    base = nombre.rsplit("/", 1)[-1].rsplit(":", 1)[-1]
    # ".pdf.pdf", ".PDF"
    while re.search(r"\.(pdf|xml|zip)$", base, re.I):
        base = re.sub(r"\.(pdf|xml|zip)$", "", base, flags=re.I)
    base = _RE_COPIA.sub("", base)
    b = _sin_acentos(base)
    salida: list[Candidato] = []

    # Con prefijo de OC: "PO-NHY13E2871", "PO D1602209 D99", "OC.732274",
    # "Pedido 934541", "Purchase Order_4504027988", "OC_GRUPOMONSORT_CL-0039"
    m = re.match(
        r"^\s*(?:PURCHASE[\s_]*ORDER|ORDEN[\s_]*DE[\s_]*COMPRA|PEDIDO|P\.?O\.?|O\.?C\.?)"
        r"(?=[\s_\-.#:])[\s_\-.#:]*(.*)$", b)
    if m:
        for tok in re.split(r"[\s_]+", m.group(1)):
            limpio = limpiar_token(tok)
            if _RE_VERSION.match(limpio):
                break
            if es_token_valido(limpio):
                salida.append(Candidato(quitar_prefijo_etiqueta(limpio), 80, "archivo",
                                        "nombre con prefijo PO/OC"))
                break
        return salida

    # Sin prefijo: el primer token con forma de número ("63600796",
    # "26LAG0200", "40328-00", "BOS-507-91753").
    for tok in re.split(r"[\s_]+", b):
        limpio = limpiar_token(tok)
        if _RE_VERSION.match(limpio):
            continue
        if es_token_valido(limpio):
            salida.append(Candidato(quitar_prefijo_etiqueta(limpio), 60, "archivo",
                                    "nombre de archivo"))
            break
    return salida


def _candidatos_sueltos(texto: str) -> list[Candidato]:
    """Último recurso: tokens con forma de OC sin etiqueta. Puntos mínimos."""
    salida = []
    t = _sin_acentos(texto or "")[:1500]
    for m in re.finditer(r"[A-Z0-9#][A-Z0-9\-/.#]{2,29}", t):
        limpio = limpiar_token(m.group(0))
        if not es_token_valido(limpio):
            continue
        antes = t[max(0, m.start() - 30):m.start()]
        if _RE_CONTEXTO_MALO.search(antes):
            continue
        # Cantidades con unidad: "10000 litros", "20 KG"
        despues = t[m.end():m.end() + 12]
        if re.match(r"\s*(LITROS?|LTS?|KGS?|KILOS?|MM|CM|PULG|PIEZAS?|PZAS?|PCS|W|V|HP|GAL|METROS?|M2|M3|MTS?)\b", despues):
            continue
        salida.append(Candidato(limpio, 10, "suelto", "sin etiqueta"))
    return salida


# ═══════════════════════════════════════════════════════════════════════════
# DECISIÓN
# ═══════════════════════════════════════════════════════════════════════════

_FAMILIA = {"pdf": "doc", "pdf_layout": "doc", "cuerpo": "doc",
            "asunto": "asunto", "archivo": "archivo", "suelto": "suelto"}


def _elegir(candidatos: list[Candidato]) -> ResultadoOC:
    if not candidatos:
        return ResultadoOC(None, "ninguna")

    # Agrupar por llave; luego fusionar grupos que comparten núcleo numérico
    # ("C-0039" del PDF con "CL-0039" del nombre de archivo).
    grupos: dict[str, list[Candidato]] = {}
    for c in candidatos:
        k = clave_oc(c.valor)
        if k:
            grupos.setdefault(k, []).append(c)

    llaves = list(grupos)
    for i, a in enumerate(llaves):
        for b in llaves[i + 1:]:
            if a not in grupos or b not in grupos:
                continue
            na, nb = nucleo_numerico(a), nucleo_numerico(b)
            if na and na == nb and (
                a.endswith(b) or b.endswith(a) or a in b or b in a
                # "C0039" (PDF) y "CL0039" (archivo): mismo número, el
                # prefijo de letras de uno extiende al del otro.
                or (a.endswith(na) and b.endswith(nb)
                    and a[:1] == b[:1] and a[:1].isalpha())
            ):
                grupos[a].extend(grupos.pop(b))

    mejor = None
    for k, cs in grupos.items():
        familias = {_FAMILIA[c.fuente] for c in cs}
        puntos = max(c.puntos for c in cs) + 25 * (len(familias) - 1)
        # Representante: la forma más completa entre las fuentes serias
        serios = [c for c in cs if c.puntos >= 60] or cs
        valor = max(serios, key=lambda c: (len(clave_oc(c.valor) or ""), c.puntos)).valor
        if mejor is None or puntos > mejor[0]:
            mejor = (puntos, valor, sorted({c.fuente for c in cs}))

    puntos, valor, fuentes = mejor
    if puntos >= 100:
        confianza = "alta"
    elif puntos >= 75:
        confianza = "media"
    elif puntos >= 50:
        confianza = "baja"
    else:
        # Un número suelto sin ninguna etiqueta no se adivina: mejor vacío y
        # que el usuario lo capture, que un "2026" que parece dato real.
        return ResultadoOC(None, "ninguna", puntos, fuentes, candidatos)
    return ResultadoOC(valor, confianza, puntos, fuentes, candidatos)


def detectar_numero_oc(
    *,
    texto_pdf: str | None = None,
    palabras_pdf: list[dict] | None = None,
    asunto: str | None = None,
    nombre_archivo: str | None = None,
    cuerpo: str | None = None,
    permitir_sueltos: bool = False,
) -> ResultadoOC:
    """Punto de entrada. Cualquier fuente puede faltar."""
    cands: list[Candidato] = []

    if texto_pdf:
        # El encabezado pesa más: una OC mencionada en las cláusulas de la
        # página 3 no es la OC del documento.
        cabeza = texto_pdf[:2500]
        cands += _candidatos_etiquetados(cabeza, "pdf", 100, 75)
    cands += _candidatos_layout(palabras_pdf)

    if asunto and not _RE_ASUNTO_RESPUESTA.match(asunto):
        cands += _candidatos_etiquetados(asunto, "asunto", 90, 85)
    elif asunto:
        # En una respuesta el asunto sigue siendo evidencia, pero más débil.
        cands += _candidatos_etiquetados(asunto, "asunto", 70, 60)

    if cuerpo:
        cands += _candidatos_etiquetados(cuerpo[:6000], "cuerpo", 85, 65)

    cands += _candidatos_archivo(nombre_archivo)

    # Corroboración: un número del nombre/asunto que aparece literal en el
    # documento vale más aunque el documento no lo etiquete (Parker pone su
    # número junto a "Bill To:").
    doc = _sin_acentos(" ".join(x for x in (texto_pdf, cuerpo) if x))
    doc_llave = re.sub(r"[^A-Z0-9]", "", doc)
    for c in list(cands):
        if c.fuente in ("archivo", "asunto"):
            k = clave_oc(c.valor)
            if k and len(k) >= 4 and k in doc_llave:
                cands.append(Candidato(c.valor, 60, "pdf", "aparece en el documento"))

    if permitir_sueltos or not cands:
        if texto_pdf:
            cands += _candidatos_sueltos(texto_pdf)
    return _elegir(cands)


def extraer_numero_oc(texto: str | None) -> str | None:
    """
    Compatibilidad: número de OC de un texto libre (descripción de concepto
    del CFDI, asunto, nombre de archivo).

    A diferencia de la versión anterior, NO adivina: sin etiqueta devuelve
    None. En una factura eso manda a "Requiere captura manual", que es lo
    correcto — antes "servicio de pipa de 10000 litros" quedaba con OC 10000.
    """
    if not texto:
        return None
    cands = _candidatos_etiquetados(texto, "pdf", 100, 80)
    # "#PO-VSM-14851": el número ES "PO-VSM-...", la etiqueta está pegada.
    for m in re.finditer(r"#\s?(P\.?O\.?-[A-Z]{2,5}-\d{3,})", _sin_acentos(texto)):
        cands.append(Candidato(m.group(1), 100, "pdf", "PO con prefijo"))
    # "#CL-0308", "#OC0415", "#4500043950": el '#' también es etiqueta
    for m in re.finditer(r"(?<![A-Z0-9])#\s?([A-Z0-9][A-Z0-9\-]{2,29})", _sin_acentos(texto)):
        limpio = limpiar_token(m.group(1))
        if es_token_valido(limpio):
            cands.append(Candidato(limpio, 80, "pdf", "precedido de #"))
    resultado = _elegir(cands)
    return resultado.numero


# ═══════════════════════════════════════════════════════════════════════════
# ¿ES UNA ORDEN DE COMPRA?
# ═══════════════════════════════════════════════════════════════════════════

_RE_SENAL_OC = re.compile(
    r"PURCHASE\s+ORDER|ORDEN\s+DE\s+COMPRA|\bP\.?O\.?\s*(NUMBER|NO\.?|#)|"
    r"NUMERO\s+DE\s+OC|\bPEDIDO\b|PO/REFERENCE|ORDEN\s+DE\s+SERVICIO|"
    r"\bP\.?O\.?\s+(?:REVISION|DATE|FECHA)\b"
)
_RE_SENAL_NO_OC = re.compile(
    r"\bCOTIZACI[OÓ]N\b|\bQUOTE\b|\bQUOTATION\b|\bPROPOSAL\b|\bPROPUESTA\b|"
    r"\bSALES\s+RECEIPT\b|\bRECIBO\b|\bRECEIPT\b|FOLIO\s+FISCAL|\bCFDI\b|"
    r"COMPLEMENTO\s+DE\s+PAGO|RECEPCI[OÓ]N\s+DE\s+PAGOS|ESTADO\s+DE\s+CUENTA|"
    r"\bINVOICE\b|\bFACTURA\b|NOTA\s+DE\s+CR[EÉ]DITO|PACKING\s+LIST|REMISI[OÓ]N"
)
_RE_NOMBRE_OC = re.compile(
    r"^\s*(PO|P\.O|OC|O\.C|PEDIDO|PURCHASE[\s_]*ORDER|ORDEN[\s_]*DE[\s_]*COMPRA)"
    r"(?=[\s_\-.#:\d])", re.I)
_RE_NOMBRE_NO_OC = re.compile(
    r"COTIZACI|QUOTE|PROPOSAL|RECEIPT|RECIBO|FACTURA|INVOICE|\bCFDI|^CP[\s_\-]?\d|"
    r"TERMS|CONDICIONES|CATALOG", re.I)


def es_documento_oc(texto: str | None, nombre_archivo: str | None = None,
                    asunto: str | None = None) -> bool:
    """
    Decide si un PDF es una orden de compra.

    - Encabezado con "Purchase Order"/"Orden de compra"/"PO Number" -> sí,
      salvo que el MISMO encabezado diga antes "Cotización", "Quote",
      "Receipt"... (la cotización de Termic dice "favor de enviar su PO").
    - PDF escaneado sin texto: decide el nombre del archivo o el asunto.
    """
    cabeza = _sin_acentos(texto or "")[:600]
    nombre = _sin_acentos(nombre_archivo or "")
    if cabeza.strip():
        si = _RE_SENAL_OC.search(cabeza)
        no = _RE_SENAL_NO_OC.search(cabeza)
        if si and (not no or si.start() < no.start()):
            return True
        if no:
            return False
        # Sin señales en el encabezado: el nombre decide ("63600796.pdf" de
        # Parker sí dice "Orden de compra" arriba; esto es para los raros).
        return bool(_RE_NOMBRE_OC.search(nombre)) and not _RE_NOMBRE_NO_OC.search(nombre)

    # Escaneado (sin capa de texto)
    if _RE_NOMBRE_NO_OC.search(nombre):
        return False
    if _RE_NOMBRE_OC.search(nombre):
        return True
    a = _sin_acentos(asunto or "")
    return bool(_RE_SENAL_OC.search(a) or re.search(r"\b(PO|OC)\b", a))


# ═══════════════════════════════════════════════════════════════════════════
# CUERPO DEL CORREO
# ═══════════════════════════════════════════════════════════════════════════

class _HtmlATexto(HTMLParser):
    _BLOQUE = {"p", "div", "br", "tr", "li", "h1", "h2", "h3", "h4", "table", "td", "th"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.partes: list[str] = []
        self._ignorar = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "head"):
            self._ignorar += 1
        elif tag in self._BLOQUE:
            self.partes.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style", "head"):
            self._ignorar = max(0, self._ignorar - 1)
        elif tag in self._BLOQUE:
            self.partes.append("\n")

    def handle_data(self, data):
        if not self._ignorar:
            self.partes.append(data)


def html_a_texto(contenido: str) -> str:
    p = _HtmlATexto()
    try:
        p.feed(contenido)
        p.close()
    except Exception:
        return re.sub(r"<[^>]+>", " ", html.unescape(contenido))
    texto = "".join(p.partes).replace("\xa0", " ")
    texto = re.sub(r"[ \t]+", " ", texto)
    texto = re.sub(r"\n\s*\n+", "\n", texto)
    return texto.strip()


_RE_CUERPO_ES_OC = re.compile(
    r"PURCHASE\s+ORDER|ORDEN\s+DE\s+COMPRA|PO/REFERENCE|\bPO\s*(NUMBER|NO\.?|#)"
)


def cuerpo_es_orden_de_compra(asunto: str | None, cuerpo: str | None,
                              nombre_empresa: str = "MONSORT") -> ResultadoOC | None:
    """
    Para correos SIN adjunto que traen la OC en el cuerpo (SciQuest/Jaggaer,
    Coupa, Ariba). Criterios, todos obligatorios:

      1. El asunto NO es una respuesta ("RE:"): una conversación sobre una
         OC no es la OC.
      2. El cuerpo tiene estructura de OC ("Purchase Order", "PO/Reference").
      3. El cuerpo menciona a la empresa (es una OC PARA Monsort).
      4. Hay número con confianza media o alta, y aparece en el cuerpo.
    """
    if not cuerpo or (asunto and _RE_ASUNTO_RESPUESTA.match(asunto)):
        return None
    c = _sin_acentos(cuerpo)
    if not _RE_CUERPO_ES_OC.search(c) or nombre_empresa.upper() not in c:
        return None
    r = detectar_numero_oc(asunto=asunto, cuerpo=cuerpo)
    if not r.numero or r.confianza not in ("alta", "media"):
        return None
    if clave_oc(r.numero) not in re.sub(r"[^A-Z0-9]", "", c):
        return None
    return r
