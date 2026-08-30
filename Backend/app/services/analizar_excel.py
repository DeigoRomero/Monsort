"""
Fase 1 de la migracion del historico: LEE y REPORTA. No toca la base de datos.

Uso (desde backend/):
    python -m app.services.analizar_excel ruta/al/GENERAL_2026.xlsx
    python -m app.services.analizar_excel ruta.xlsx --csv salida.csv

El mismo modulo se reutiliza despues como fase de dry-run: `leer_excel()`
devuelve las filas ya normalizadas y los rechazos por separado.
"""

import re
import sys
import csv
import datetime as dt
from collections import Counter, defaultdict
from statistics import median
from decimal import Decimal, InvalidOperation

import openpyxl


# ── Posiciones de columna (1-indexed), fijas en todas las hojas ──────────
COL_FOLIO_FISCAL = 1
COL_CLIENTE      = 2
COL_RFC          = 3
COL_COFIDI       = 4   # ignorada a proposito
COL_FOLIO_INTERNO= 5
COL_NUMERO_OC    = 6
COL_FECHA        = 7
COL_MONEDA       = 8
COL_SUBTOTAL     = 9
COL_IVA          = 10
COL_TOTAL        = 11
COL_FECHA_PROB   = 12
COL_FECHA_LIQ    = 13
COL_TIPO_CAMBIO  = 14

HOJAS_IGNORADAS = {"Hoja 5"}

# El orden cronologico se toma del propio libro (wb.sheetnames ya viene
# en orden). No se codifica aqui: las hojas se renombran entre versiones
# del archivo ("ENERO-26" paso a ser "ene-26").

# Minimo de tipos de cambio observados para confiar en la mediana del mes.
MIN_MUESTRAS_TC = 3

RE_UUID = re.compile(
    r"^[0-9A-F]{8}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{12}$"
)
RE_HEX32 = re.compile(r"^[0-9A-F]{32}$")
RE_RFC = re.compile(r"^[A-ZÑ&]{3,4}\d{6}[A-Z0-9]{3}$")
RE_CANCELADA = re.compile(r"CANCEL", re.IGNORECASE)


# ── Limpiadores ──────────────────────────────────────────────────────────

def limpiar_uuid(valor):
    """
    Normaliza el folio fiscal. Devuelve (uuid, motivo_rechazo).

    El Excel trae UUIDs con espacios en vez de guion, guiones faltantes y
    basura de encoding (_x0002_). Se reconstruye a partir de los 32 hex.
    """
    if valor is None:
        return None, "sin folio fiscal"

    texto = str(valor).strip().upper()
    if not texto:
        return None, "sin folio fiscal"

    if RE_UUID.match(texto):
        return texto, None

    # Quitar todo lo que no sea hexadecimal y rearmar
    solo_hex = re.sub(r"[^0-9A-F]", "", texto.replace("_X0002_", ""))
    if RE_HEX32.match(solo_hex):
        armado = (
            f"{solo_hex[0:8]}-{solo_hex[8:12]}-{solo_hex[12:16]}-"
            f"{solo_hex[16:20]}-{solo_hex[20:32]}"
        )
        return armado, None

    return None, f"folio fiscal ilegible ({texto[:40]})"


def limpiar_rfc(valor):
    if valor is None:
        return None
    texto = re.sub(r"[\s\-\.]", "", str(valor)).upper()
    return texto if RE_RFC.match(texto) else None


def limpiar_moneda(valor, tipo_cambio):
    """MXN | USD. US y DLLS son la misma cosa; vacio se infiere por el TC."""
    if valor is not None:
        texto = str(valor).strip().upper()
        if texto in ("MXN", "PESOS", "MN", "M.N."):
            return "MXN"
        if texto in ("USD", "US", "DLLS", "DLS", "DOLARES", "USD."):
            return "USD"
    if tipo_cambio is not None and tipo_cambio > 1:
        return "USD"
    return "MXN"


def limpiar_numero_oc(valor):
    """Viene como float (878738.0), str, o datetime por formato de celda."""
    if valor is None:
        return None
    if isinstance(valor, float) and valor.is_integer():
        return str(int(valor))
    if isinstance(valor, int):
        return str(valor)
    if isinstance(valor, dt.datetime):
        return valor.strftime("%d/%m/%Y")
    texto = str(valor).strip()
    return texto or None


def limpiar_decimal(valor):
    if valor is None:
        return None
    if isinstance(valor, (int, float)):
        return Decimal(str(round(float(valor), 6)))
    try:
        limpio = re.sub(r"[^\d\.\-]", "", str(valor))
        return Decimal(limpio) if limpio else None
    except (InvalidOperation, ValueError):
        return None


def limpiar_fecha(valor):
    """Devuelve date o None. El texto (CANCELADA, CP553...) se ignora aqui."""
    if isinstance(valor, dt.datetime):
        # Fechas tipo 10/08/0206: ano imposible
        if valor.year < 2000 or valor.year > 2100:
            return None
        return valor.date()
    if isinstance(valor, dt.date):
        return valor
    return None


def limpiar_tipo_cambio(valor):
    """Las celdas formateadas como fecha por error no son tipos de cambio."""
    if isinstance(valor, (dt.datetime, dt.date)):
        return None
    d = limpiar_decimal(valor)
    if d is None:
        return None
    return d if Decimal("1") <= d <= Decimal("100") else None


# Prefijos de folio interno observados en el historico.
# El Excel mezcla facturas y complementos de pago en la misma tabla.
PREFIJOS_FACTURA = ("FG", "FS", "FF", "BJ", "CL")
PREFIJOS_COMPLEMENTO = ("CP",)


def clasificar_documento(folio_interno, total):
    """factura | complemento | desconocido"""
    if folio_interno:
        prefijo = re.match(r"^([A-Za-z]+)", folio_interno.strip())
        if prefijo:
            p = prefijo.group(1).upper()
            if p in PREFIJOS_COMPLEMENTO:
                return "complemento"
            if p in PREFIJOS_FACTURA:
                return "factura"
            return "desconocido"
    # Sin folio interno: si trae importe se asume factura
    return "factura" if total else "desconocido"


def detectar_cancelacion(fila_cruda):
    """La cancelacion viene como texto dentro de columnas de fecha."""
    for col in (COL_FECHA_PROB, COL_FECHA_LIQ, COL_TIPO_CAMBIO):
        v = fila_cruda.get(col)
        if isinstance(v, str) and RE_CANCELADA.search(v):
            return True, v.strip()
    return False, None


# ── Lectura ──────────────────────────────────────────────────────────────

def _texto(celdas, col):
    v = celdas.get(col)
    if v is None:
        return ""
    return str(v).strip()


def _es_fila_header(celdas):
    """Los encabezados se repiten una vez por bloque de cliente."""
    if "folio fiscal" in _texto(celdas, COL_FOLIO_FISCAL).lower():
        return True
    # Algunos bloques traen la primera celda con otro texto ("FOLIO")
    return (_texto(celdas, COL_CLIENTE).lower() == "cliente"
            and _texto(celdas, COL_RFC).upper().replace(".", "") == "RFC")


def _es_ruido(celdas):
    """
    Filas que existen por el formato del Excel y no representan documentos:
    titulo del bloque, la marca 'ok' y la fila de totales.

    Se descartan en silencio: no son rechazos, no hay nada que revisar.
    """
    if _texto(celdas, COL_FOLIO_FISCAL).lower() in ("ok", "okay", "ok."):
        return True

    # El RFC NO cuenta como sustancia: viene arrastrado por autofill en
    # decenas de filas vacias debajo de cada bloque. Una fila real tiene
    # algo que la identifique como documento.
    tiene_sustancia = any([
        _parece_folio_fiscal(celdas.get(COL_FOLIO_FISCAL)),
        _texto(celdas, COL_FOLIO_INTERNO) != "",
        isinstance(celdas.get(COL_FECHA), (dt.datetime, dt.date)),
    ])
    return not tiene_sustancia


def _parece_folio_fiscal(valor):
    """
    Distingue un UUID (aunque venga roto) del nombre del cliente que
    ocupa esa misma celda en la fila de titulo de cada bloque.
    Un UUID tiene 32 caracteres hexadecimales; un nombre, muchos menos.
    """
    if valor is None:
        return False
    hexes = re.sub(r"[^0-9A-F]", "", str(valor).upper())
    return len(hexes) >= 24


def leer_excel(ruta):
    """
    Devuelve (filas, rechazos).
      filas    -> dicts normalizados, listos para insertar
      rechazos -> dicts con hoja, fila, motivo y datos crudos
    """
    wb = openpyxl.load_workbook(ruta, data_only=True)
    filas, rechazos = [], []
    vistos = {}   # folio_fiscal -> (hoja, fila)
    n_ruido = 0
    orden_hojas = [h for h in wb.sheetnames if h not in HOJAS_IGNORADAS]

    for ws in wb.worksheets:
        if ws.title in HOJAS_IGNORADAS:
            continue

        for r in range(1, ws.max_row + 1):
            celdas = {
                c: ws.cell(row=r, column=c).value
                for c in range(1, COL_TIPO_CAMBIO + 1)
            }

            if _es_fila_header(celdas):
                continue
            if _es_ruido(celdas):
                n_ruido += 1
                continue

            rfc = limpiar_rfc(celdas.get(COL_RFC))
            nombre = (str(celdas.get(COL_CLIENTE)).strip()
                      if celdas.get(COL_CLIENTE) else None)

            if not rfc:
                rechazos.append({
                    "hoja": ws.title, "fila": r,
                    "motivo": "RFC invalido o ausente",
                    "cliente": nombre,
                    "folio_interno": celdas.get(COL_FOLIO_INTERNO),
                })
                continue

            folio_fiscal, motivo = limpiar_uuid(celdas.get(COL_FOLIO_FISCAL))
            if folio_fiscal is None:
                rechazos.append({
                    "hoja": ws.title, "fila": r, "motivo": motivo,
                    "cliente": nombre,
                    "folio_interno": celdas.get(COL_FOLIO_INTERNO),
                })
                continue

            if folio_fiscal in vistos:
                h0, r0 = vistos[folio_fiscal]
                rechazos.append({
                    "hoja": ws.title, "fila": r,
                    "motivo": f"folio fiscal duplicado (ya en {h0} fila {r0})",
                    "cliente": nombre,
                    "folio_interno": celdas.get(COL_FOLIO_INTERNO),
                })
                continue
            vistos[folio_fiscal] = (ws.title, r)

            tipo_cambio = limpiar_tipo_cambio(celdas.get(COL_TIPO_CAMBIO))
            cancelada, texto_cancelacion = detectar_cancelacion(celdas)
            folio_interno = (str(celdas.get(COL_FOLIO_INTERNO)).strip()
                             if celdas.get(COL_FOLIO_INTERNO) else None)
            total = limpiar_decimal(celdas.get(COL_TOTAL))

            filas.append({
                "tipo_documento": clasificar_documento(folio_interno, total),
                "hoja": ws.title,
                "fila_excel": r,
                "folio_fiscal": folio_fiscal,
                "rfc": rfc,
                "cliente": nombre,
                "folio_interno": folio_interno,
                "numero_oc": limpiar_numero_oc(celdas.get(COL_NUMERO_OC)),
                "fecha": limpiar_fecha(celdas.get(COL_FECHA)),
                "moneda": limpiar_moneda(celdas.get(COL_MONEDA), tipo_cambio),
                "tipo_cambio": tipo_cambio,
                "tipo_cambio_estimado": False,
                "tipo_cambio_fuente": ("capturado" if tipo_cambio is not None
                                       else None),
                "subtotal": limpiar_decimal(celdas.get(COL_SUBTOTAL)),
                "iva": limpiar_decimal(celdas.get(COL_IVA)),
                "total": total,
                "fecha_probable": limpiar_fecha(celdas.get(COL_FECHA_PROB)),
                "fecha_liquidacion": limpiar_fecha(celdas.get(COL_FECHA_LIQ)),
                "cancelada": cancelada,
                "texto_cancelacion": texto_cancelacion,
            })

    return filas, rechazos, n_ruido, orden_hojas


# ── Consolidacion de RFC ────────────────────────────────────────────────

def consolidar_rfcs(filas):
    """
    Corrige los RFC danados por autofill de Excel (el ultimo caracter se
    incrementa al arrastrar: ...MQ5, MQ6, MQ7). Agrupa por nombre de
    cliente y se queda con el RFC mayoritario del grupo.

    Devuelve (mapa_correccion, grupos) y NO modifica las filas.
    """
    por_nombre = defaultdict(Counter)
    for f in filas:
        if f["cliente"]:
            por_nombre[f["cliente"].upper()][f["rfc"]] += 1

    mapa, grupos = {}, []
    for nombre, cuenta in por_nombre.items():
        if len(cuenta) == 1:
            continue
        canonico, n_canonico = cuenta.most_common(1)[0]
        variantes = {r: n for r, n in cuenta.items() if r != canonico}
        for variante in variantes:
            mapa[variante] = canonico
        grupos.append({
            "nombre": nombre,
            "canonico": canonico,
            "n_canonico": n_canonico,
            "variantes": variantes,
        })
    return mapa, grupos


def aplicar_consolidacion(filas, mapa):
    for f in filas:
        if f["rfc"] in mapa:
            f["rfc_original"] = f["rfc"]
            f["rfc"] = mapa[f["rfc"]]


# ── Tipo de cambio estimado ─────────────────────────────────────────────

def calcular_tc_por_mes(filas, orden_hojas):
    """
    Mediana del tipo de cambio realmente capturado en cada hoja.

    Los meses con menos de MIN_MUESTRAS_TC valores no dan una mediana
    confiable (febrero no tiene ninguno, junio tiene uno), asi que se
    interpolan linealmente entre los meses vecinos que si tienen datos.

    Devuelve {hoja: (valor, fuente)} donde fuente es 'mediana' o 'interpolado'.
    """
    observados = defaultdict(list)
    for f in filas:
        if f["moneda"] == "USD" and f["tipo_cambio"] is not None:
            observados[f["hoja"]].append(float(f["tipo_cambio"]))

    solidas = {
        h: median(v) for h, v in observados.items()
        if len(v) >= MIN_MUESTRAS_TC
    }
    if not solidas:
        return {}

    tabla = {h: (v, "mediana") for h, v in solidas.items()}

    for i, hoja in enumerate(orden_hojas):
        if hoja in tabla:
            continue
        anterior = next(
            (orden_hojas[j] for j in range(i - 1, -1, -1)
             if orden_hojas[j] in solidas), None
        )
        siguiente = next(
            (orden_hojas[j] for j in range(i + 1, len(orden_hojas))
             if orden_hojas[j] in solidas), None
        )
        if anterior and siguiente:
            ia, isig = orden_hojas.index(anterior), orden_hojas.index(siguiente)
            peso = (i - ia) / (isig - ia)
            valor = solidas[anterior] + peso * (solidas[siguiente] - solidas[anterior])
        elif anterior:
            valor = solidas[anterior]
        elif siguiente:
            valor = solidas[siguiente]
        else:
            continue
        tabla[hoja] = (valor, "interpolado")

    return tabla


def aplicar_tc_banxico(filas):
    """
    Rellena el tipo de cambio consultando el FIX historico de Banxico.
    Es la fuente preferida: es el dato oficial, no una estimacion.

    Devuelve (n_rellenadas, n_sin_dato). Si el token no esta configurado
    o la API falla, propaga el error para que el llamador decida.
    """
    from app.services.banxico import obtener_tc_rango, tc_para_factura

    pendientes = [f for f in filas
                  if f["moneda"] == "USD" and f["tipo_cambio"] is None
                  and f["fecha"]]
    if not pendientes:
        return 0, 0

    fechas = [f["fecha"] for f in pendientes]
    # margen hacia atras: el TC aplicable es el del dia habil anterior
    tabla = obtener_tc_rango(min(fechas) - dt.timedelta(days=10), max(fechas))

    rellenadas = sin_dato = 0
    for f in pendientes:
        tc = tc_para_factura(tabla, f["fecha"])
        if tc is None:
            sin_dato += 1
            continue
        f["tipo_cambio"] = tc
        f["tipo_cambio_estimado"] = False   # dato oficial, no estimado
        f["tipo_cambio_fuente"] = "banxico"
        rellenadas += 1
    return rellenadas, sin_dato


def aplicar_tc_estimado(filas, tabla):
    """
    Rellena el tipo de cambio faltante en las facturas en USD.
    Marca tipo_cambio_estimado para poder distinguir despues lo capturado
    de lo rellenado por el importador.
    """
    n = 0
    for f in filas:
        if f["moneda"] != "USD" or f["tipo_cambio"] is not None:
            continue
        entrada = tabla.get(f["hoja"])
        if not entrada:
            continue
        f["tipo_cambio"] = Decimal(str(round(entrada[0], 4)))
        f["tipo_cambio_estimado"] = True
        f["tipo_cambio_fuente"] = "mediana_mensual"
        n += 1
    return n


# ── Reporte ──────────────────────────────────────────────────────────────

def _titulo(texto):
    print()
    print("=" * 68)
    print(texto)
    print("=" * 68)


def reportar(filas, rechazos, grupos, n_ruido=0, tabla_tc=None, orden_hojas=None):
    _titulo("RESUMEN")
    print(f"  Filas importables : {len(filas)}")
    print(f"  Filas rechazadas  : {len(rechazos)}")
    print(f"  Ruido de formato  : {n_ruido}  (titulos, 'ok', totales)")

    por_hoja = Counter(f["hoja"] for f in filas)
    print("\n  Por hoja:")
    for hoja, n in por_hoja.items():
        print(f"    {hoja:<14} {n:>4}")

    _titulo("CLIENTES (RFC consolidado)")
    clientes = defaultdict(lambda: {"n": 0, "nombres": Counter()})
    for f in filas:
        c = clientes[f["rfc"]]
        c["n"] += 1
        if f["cliente"]:
            c["nombres"][f["cliente"]] += 1
    print(f"  Total: {len(clientes)} clientes\n")
    for rfc, d in sorted(clientes.items(), key=lambda x: -x[1]["n"]):
        nombre = d["nombres"].most_common(1)[0][0] if d["nombres"] else "?"
        print(f"    {rfc:<14} {d['n']:>4}  {nombre[:42]}")

    if grupos:
        _titulo("RFC CORREGIDOS (autofill de Excel)")
        for g in grupos:
            print(f"    {g['nombre'][:44]}")
            print(f"       canonico  {g['canonico']}  ({g['n_canonico']} filas)")
            for v, n in g["variantes"].items():
                print(f"       corregido {v}  ({n} filas)")

    _titulo("CALIDAD DE DATOS")
    total = len(filas) or 1
    campos = ["folio_interno", "numero_oc", "fecha", "total",
              "subtotal", "iva", "fecha_probable", "fecha_liquidacion"]
    for campo in campos:
        n = sum(1 for f in filas if f[campo] is not None)
        print(f"    {campo:<20} {n:>4} / {len(filas)}  ({100*n/total:>5.1f}%)")

    print()
    print(f"    moneda MXN           {sum(1 for f in filas if f['moneda']=='MXN'):>4}")
    print(f"    moneda USD           {sum(1 for f in filas if f['moneda']=='USD'):>4}")
    usd_sin_tc = sum(1 for f in filas
                     if f["moneda"] == "USD" and f["tipo_cambio"] is None)
    print(f"      USD sin tipo_cambio{usd_sin_tc:>4}   <- requieren captura")
    print(f"    marcadas canceladas  {sum(1 for f in filas if f['cancelada']):>4}")

    _titulo("TIPO DE CAMBIO ESTIMADO")
    tabla = tabla_tc or {}
    print(f'    {"hoja":<14} {"valor":>9}  {"fuente":<12} {"rellena":>8}')
    for hoja in (orden_hojas or []):
        if hoja not in tabla:
            continue
        valor, fuente = tabla[hoja]
        n = sum(1 for f in filas
                if f["hoja"] == hoja and f["tipo_cambio_estimado"])
        print(f"    {hoja:<14} {valor:>9.4f}  {fuente:<12} {n:>8}")
    fuentes = Counter(f["tipo_cambio_fuente"] for f in filas
                      if f["moneda"] == "USD")
    print("\n    Fuente del tipo de cambio (USD):")
    for fuente, n in fuentes.most_common():
        print(f"      {str(fuente):<18} {n:>4}")

    _titulo("TIPO DE DOCUMENTO")
    tipos = Counter(f["tipo_documento"] for f in filas)
    for t, n in tipos.most_common():
        print(f"    {t:<14} {n:>4}")
    print("\n    Los complementos NO deben entrar como facturas.")
    desconocidos = Counter(
        re.match(r"^([A-Za-z]+)", f["folio_interno"]).group(1).upper()
        for f in filas
        if f["tipo_documento"] == "desconocido" and f["folio_interno"]
        and re.match(r"^([A-Za-z]+)", f["folio_interno"])
    )
    if desconocidos:
        print("\n    Prefijos sin clasificar:")
        for p_, n in desconocidos.most_common():
            print(f"      {p_:<8} {n:>4}  <- confirmar que tipo de documento es")

    _titulo("OC REPETIDAS ENTRE CLIENTES DISTINTOS")
    oc_map = defaultdict(set)
    for f in filas:
        if f["numero_oc"]:
            oc_map[f["numero_oc"]].add(f["rfc"])
    choques = {k: v for k, v in oc_map.items() if len(v) > 1}
    if choques:
        for oc, rfcs in list(choques.items())[:20]:
            print(f"    {oc:<26} {sorted(rfcs)}")
        print(f"\n    {len(choques)} numeros de OC en mas de un cliente.")
        print("    Confirma que la llave sea (cliente, numero_oc).")
    else:
        print("    Ninguna.")

    _titulo("RECHAZOS")
    for motivo, n in Counter(
        re.sub(r"\(.*\)", "(...)", r["motivo"]) for r in rechazos
    ).most_common():
        print(f"    {n:>4}  {motivo}")
    print()
    for r in rechazos:
        print(f"    {r['hoja']:<12} fila {r['fila']:<5} "
              f"{str(r.get('cliente'))[:26]:<28} "
              f"folio={str(r.get('folio_interno'))[:12]:<14} {r['motivo']}")


def exportar_csv(filas, ruta):
    if not filas:
        return
    columnas = [k for k in filas[0].keys() if k != "rfc_original"]
    with open(ruta, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.DictWriter(fh, fieldnames=columnas, extrasaction="ignore")
        w.writeheader()
        w.writerows(filas)
    print(f"\nCSV escrito en {ruta}")


def main():
    if len(sys.argv) < 2:
        print("Uso: python -m app.services.analizar_excel <archivo.xlsx> [--csv salida.csv]")
        sys.exit(1)

    ruta = sys.argv[1]
    filas, rechazos, n_ruido, orden_hojas = leer_excel(ruta)
    mapa, grupos = consolidar_rfcs(filas)
    aplicar_consolidacion(filas, mapa)

    # Banxico primero (dato oficial); la mediana mensual solo cubre
    # lo que Banxico no pudo resolver.
    if "--banxico" in sys.argv:
        try:
            n, sin_dato = aplicar_tc_banxico(filas)
            print(f"Banxico: {n} tipos de cambio obtenidos"
                  + (f", {sin_dato} sin dato" if sin_dato else ""))
        except Exception as e:
            print(f"Banxico no disponible ({e}); se usara la mediana mensual.")

    tabla_tc = calcular_tc_por_mes(filas, orden_hojas)
    aplicar_tc_estimado(filas, tabla_tc)
    reportar(filas, rechazos, grupos, n_ruido, tabla_tc, orden_hojas)

    if "--csv" in sys.argv:
        destino = sys.argv[sys.argv.index("--csv") + 1]
        exportar_csv(filas, destino)


if __name__ == "__main__":
    main()