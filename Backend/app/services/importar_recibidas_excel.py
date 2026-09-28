r"""
Importa a FacturasRecibidas el Excel de gastos que mando el cliente
("GASTOS FACTURAS RECIBIDAS.xlsx": una hoja por mes, abril a septiembre 2026).

Corre en seco por defecto. Solo escribe con --apply.

    python -m app.services.importar_recibidas_excel "GASTOS FACTURAS RECIBIDAS.xlsx"
    python -m app.services.importar_recibidas_excel "GASTOS FACTURAS RECIBIDAS.xlsx" --apply
    python -m app.services.importar_recibidas_excel archivo.xlsx --reporte revision.csv

POR QUE SE LEE POR CONTENIDO Y NO POR ENCABEZADO

    Cada hoja tiene un layout distinto y los encabezados mienten:
      - ABRIL es la exportacion completa del portal del SAT (receptor, PAC,
        efecto, cancelable, estatus, proceso de cancelacion) y no trae
        encabezado.
      - MAYO a JULIO dicen FOLIO, RFC, CLIENTE, FECHA, BANCO, ESTATUS, MONTO.
      - AGOSTO y SEPTIEMBRE traen el mismo encabezado, pero los datos estan
        corridos una columna a la izquierda: el "ok" cae bajo BANCO y el
        monto bajo ESTATUS.
    Por eso cada fila se interpreta por lo que contiene: el UUID es la
    primera celda, el RFC la segunda, el nombre la tercera, y despues se
    busca la primera fecha, el primer monto y el primer "Ingreso/Egreso/
    Pago/...". Lo que queda a la derecha es informacion de estatus.

QUE SE GUARDA Y QUE NO

    origen = 'excel', id_solicitud = NULL.

    sat_estado solo se llena cuando el Excel lo dice de forma explicita
    (columna Estatus de abril, o notas de cancelacion como "Cancelado con
    aceptacion" / "CANCELADA"). En los demas casos queda NULL y lo resuelve
    la verificacion SOAP. No se asume "Vigente": el default optimista
    dejaria una cancelada disfrazada de buena (misma regla que
    parser_metadata._validar).

    Si el Excel se contradice (estatus "Vigente" con un proceso de
    cancelacion "Cancelado con aceptacion"), se deja NULL y se reporta.

    Las marcas "ok"/"OK" del cliente (columnas BANCO/ESTATUS) NO se
    importan: son su control interno de revision y FacturasRecibidas no
    tiene donde ponerlas. Se cuentan en el reporte.

    Si el folio ya existe en la tabla, la fila se SALTA (on_conflict_do_
    nothing): lo que haya entrado por el SAT manda sobre el Excel. Cuando
    llegue la metadata del SAT, su ingesta refresca sat_estado y
    fecha_cancelacion de estas filas; los hechos de emision se quedan.

DESPUES DE IMPORTAR

    La verificacion nocturna de recibidas se salta los ultimos 90 dias
    (supone que la metadata ya los cubrio, y todavia no llega) y las ya
    canceladas. Las cancelaciones de este Excel las marco el cliente a mano,
    asi que la primera pasada las incluye para que el SAT las confirme:

        python -m app.services.verificacion_recibidas_service --limite 800 --dias-cubiertos 0 --incluir-canceladas --apply
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
from collections import Counter
from datetime import datetime
from decimal import Decimal, InvalidOperation

RFC_MONSORT = "MSF140227BF7"
NOMBRE_MONSORT = "MONSORT SIN FRONTERAS"
ORIGEN = "excel"

PATRON_UUID = re.compile(
    r"^[0-9A-F]{8}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{12}$"
)
PATRON_RFC = re.compile(r"^[A-ZÑ&]{3,4}\d{6}[A-Z0-9]{3}$")

EFECTOS = {
    "INGRESO": "I",
    "EGRESO": "E",
    "PAGO": "P",
    "TRASLADO": "T",
    "NOMINA": "N",
    "NÓMINA": "N",
}

ESTADO_VIGENTE = "Vigente"
ESTADO_CANCELADO = "Cancelado"

# Notas que significan "ya esta cancelada" (proceso terminado).
NOTAS_CANCELADA = (
    "CANCELADO CON ACEPTACION",
    "CANCELADO SIN ACEPTACION",
    "CANCELADO",
    "CANCELADA",
    "PLAZO VENCIDO",
)

MESES = {
    "ENERO": 1, "FEBRERO": 2, "MARZO": 3, "ABRIL": 4, "MAYO": 5, "JUNIO": 6,
    "JULIO": 7, "AGOSTO": 8, "SEPTIEMBRE": 9, "OCTUBRE": 10,
    "NOVIEMBRE": 11, "DICIEMBRE": 12,
}


# ---------------------------------------------------------------------------
# Lectura de celdas
# ---------------------------------------------------------------------------

def _texto(valor) -> str:
    return str(valor).strip() if valor is not None else ""


def _sin_acentos(texto: str) -> str:
    return (texto.upper()
            .replace("Á", "A").replace("É", "E").replace("Í", "I")
            .replace("Ó", "O").replace("Ú", "U"))


def _como_fecha(valor):
    if isinstance(valor, datetime):
        return valor
    texto = _texto(valor)
    if re.match(r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}", texto):
        try:
            return datetime.fromisoformat(texto)
        except ValueError:
            return None
    return None


def _normalizar_separadores(texto: str) -> str | None:
    """
    Deja el numero con punto decimal y sin separador de miles.

    La segunda version del Excel trae montos escritos A MANO en formato
    europeo ('$5.560,00', '$50.000,00', '$65,00'). Quitar la coma a ciegas
    los convertia en 5.56, 50.00 y 6500. Regla: el separador que aparece
    AL FINAL es el decimal.

      '1,234.50'  -> 1234.50      '1.234,50' -> 1234.50
      '65,00'     -> 65.00        '1,234'    -> 1234
      '5.560'     -> None (ambiguo: 5.56 o 5560; se reporta, no se adivina)
    """
    tiene_coma, tiene_punto = "," in texto, "." in texto
    if tiene_coma and tiene_punto:
        if texto.rfind(",") > texto.rfind("."):
            return texto.replace(".", "").replace(",", ".")
        return texto.replace(",", "")
    if tiene_coma:
        if re.search(r",\d{1,2}$", texto) and texto.count(",") == 1:
            return texto.replace(",", ".")
        if re.match(r"^\d{1,3}(,\d{3})+$", texto):
            return texto.replace(",", "")
        return None
    if tiene_punto and re.match(r"^\d{1,3}(\.\d{3})+$", texto):
        return None
    return texto


def _como_monto(valor):
    """'$1,234.50', 1234.5, 0 -> Decimal. Cualquier otra cosa -> None."""
    if isinstance(valor, bool):
        return None
    if isinstance(valor, (int, float)):
        return Decimal(str(valor))
    texto = _texto(valor).replace(" ", "")
    if not texto:
        return None
    negativo = texto.startswith("-") or (texto.startswith("(") and texto.endswith(")"))
    texto = texto.strip("()-").replace("$", "")
    texto = _normalizar_separadores(texto)
    if texto is None or not re.match(r"^\d+(\.\d+)?$", texto):
        return None
    try:
        monto = Decimal(texto)
    except InvalidOperation:
        return None
    return -monto if negativo else monto


def _mes_de_hoja(nombre_hoja: str) -> int | None:
    limpio = _sin_acentos(nombre_hoja)
    for nombre, numero in MESES.items():
        if limpio.startswith(nombre):
            return numero
    return None


# ---------------------------------------------------------------------------
# Interpretacion de una fila
# ---------------------------------------------------------------------------

def interpretar_fila(celdas: tuple) -> tuple[dict | None, list[str]]:
    """
    Devuelve (registro, avisos). registro es None si la fila no se puede
    importar; en ese caso avisos trae el motivo.
    """
    avisos: list[str] = []
    celdas = list(celdas)

    uuid = _texto(celdas[0]).upper() if celdas else ""
    if not PATRON_UUID.match(uuid):
        return None, ["sin folio fiscal (UUID) en la primera columna"]

    rfc_emisor = _texto(celdas[1]).upper() if len(celdas) > 1 else ""
    if not PATRON_RFC.match(rfc_emisor):
        return None, [f"RFC emisor invalido {rfc_emisor!r}"]

    nombre_emisor = _texto(celdas[2]) if len(celdas) > 2 else ""

    # Fecha: primera celda con fecha despues del nombre. Lo que haya en
    # medio (abril: RFC y nombre del receptor) se revisa.
    i_fecha = next(
        (i for i in range(3, len(celdas)) if _como_fecha(celdas[i])), None
    )
    if i_fecha is None:
        return None, ["sin fecha de emision"]
    fecha = _como_fecha(celdas[i_fecha])

    rfc_receptor = RFC_MONSORT
    for valor in celdas[3:i_fecha]:
        texto = _texto(valor).upper()
        if PATRON_RFC.match(texto):
            rfc_receptor = texto
    if rfc_receptor != RFC_MONSORT:
        return None, [f"receptor {rfc_receptor} no es Monsort"]

    # Monto: primera celda con forma de monto despues de la fecha. En
    # medio puede venir el RFC del PAC (abril) o las marcas del cliente.
    i_monto = next(
        (i for i in range(i_fecha + 1, len(celdas))
         if _como_monto(celdas[i]) is not None),
        None,
    )
    if i_monto is None:
        return None, ["sin monto"]
    monto = _como_monto(celdas[i_monto])

    rfc_pac = None
    marcas_cliente = []
    for valor in celdas[i_fecha + 1:i_monto]:
        texto = _texto(valor)
        if PATRON_RFC.match(texto.upper()):
            rfc_pac = texto.upper()
        elif texto:
            marcas_cliente.append(texto)

    # Efecto: primera palabra reconocida despues del monto.
    i_efecto = next(
        (i for i in range(i_monto + 1, len(celdas))
         if _sin_acentos(_texto(celdas[i])) in EFECTOS),
        None,
    )
    efecto = EFECTOS[_sin_acentos(_texto(celdas[i_efecto]))] if i_efecto is not None else None
    if efecto is None:
        avisos.append("sin tipo de comprobante (Ingreso/Egreso/Pago)")

    # Todo lo que sigue es estatus.
    cola = celdas[(i_efecto if i_efecto is not None else i_monto) + 1:]
    textos_cola = [_sin_acentos(_texto(v)) for v in cola if _texto(v)]
    fechas_cola = [f for f in (_como_fecha(v) for v in cola) if f]

    dice_vigente = "VIGENTE" in textos_cola
    nota_cancelada = next((t for t in textos_cola if t in NOTAS_CANCELADA), None)

    sat_estado = None
    fecha_cancelacion = None
    if nota_cancelada and dice_vigente and nota_cancelada != "CANCELADO":
        # Abril, fila con estatus "Vigente" y proceso "Cancelado con
        # aceptacion". El Excel se contradice: que decida el SAT.
        avisos.append(
            f"estatus contradictorio (Vigente y {nota_cancelada.title()}): "
            "se deja sin estatus para que lo resuelva la verificacion SAT"
        )
    elif nota_cancelada:
        sat_estado = ESTADO_CANCELADO
        # Portal del SAT: [proceso, fecha solicitud, fecha cancelacion].
        if len(fechas_cola) >= 2:
            fecha_cancelacion = fechas_cola[1]
        elif fechas_cola:
            fecha_cancelacion = fechas_cola[0]
    elif dice_vigente:
        sat_estado = ESTADO_VIGENTE

    if efecto is None and nota_cancelada:
        # Version 2 del Excel: el cliente escribio "CANCELADO" ENCIMA de
        # "Ingreso". Se respeta la cancelacion; el tipo queda NULL y lo
        # rellena la metadata del SAT cuando llegue (solo llena vacios).
        avisos[:] = [a for a in avisos if not a.startswith("sin tipo")]
        avisos.append("tipo de comprobante reemplazado por la nota de cancelacion: queda sin tipo")

    if "EN PROCESO" in textos_cola:
        avisos.append("cancelacion 'En proceso' segun el Excel")

    registro = {
        "folio_fiscal": uuid,
        "rfc_emisor": rfc_emisor,
        "nombre_emisor": nombre_emisor or None,
        "rfc_receptor": RFC_MONSORT,
        "nombre_receptor": NOMBRE_MONSORT,
        "fecha_emision": fecha,
        "monto_total": monto,
        "efecto_comprobante": efecto,
        "rfc_pac": rfc_pac,
        "origen": ORIGEN,
        "sat_estado": sat_estado,
        "fecha_cancelacion": fecha_cancelacion,
        "id_solicitud": None,
        # Solo para el reporte; se quita antes de insertar.
        "_marcas_cliente": marcas_cliente,
    }
    return registro, avisos


# ---------------------------------------------------------------------------
# Libro completo
# ---------------------------------------------------------------------------

def leer_libro(ruta: str) -> dict:
    """
    Lee todas las hojas. Devuelve un dict con:
      registros   {uuid: registro} ya deduplicados
      incidencias [dict(hoja, fila, uuid, tipo, detalle)]
      por_hoja    {hoja: filas_leidas}
    """
    import openpyxl

    libro = openpyxl.load_workbook(ruta, data_only=True, read_only=True)

    registros: dict[str, dict] = {}
    origen_uuid: dict[str, tuple[str, int]] = {}
    incidencias: list[dict] = []
    por_hoja: Counter = Counter()

    def incidencia(hoja, fila, uuid, tipo, detalle):
        incidencias.append(
            {"hoja": hoja, "fila": fila, "uuid": uuid, "tipo": tipo, "detalle": detalle}
        )

    for hoja in libro.worksheets:
        mes_hoja = _mes_de_hoja(hoja.title)

        for n_fila, celdas in enumerate(hoja.iter_rows(values_only=True), start=1):
            if not celdas or not any(_texto(v) for v in celdas):
                continue

            primera = _texto(celdas[0]).upper()
            if primera in ("FOLIO", "UUID", "FOLIO FISCAL"):
                continue  # encabezado

            registro, avisos = interpretar_fila(celdas)

            if registro is None:
                # Filas basura de una sola celda ("«") no son incidencia.
                llenas = [v for v in celdas if _texto(v)]
                if len(llenas) <= 1:
                    continue
                incidencia(hoja.title, n_fila, primera or None, "no importable",
                           f"{avisos[0]} | {[ _texto(v) for v in llenas[:6] ]}")
                continue

            por_hoja[hoja.title] += 1
            uuid = registro["folio_fiscal"]

            for aviso in avisos:
                incidencia(hoja.title, n_fila, uuid, "aviso", aviso)

            if mes_hoja and registro["fecha_emision"].month != mes_hoja:
                incidencia(hoja.title, n_fila, uuid, "aviso",
                           f"fecha {registro['fecha_emision']:%Y-%m-%d} fuera del mes de la hoja")

            if uuid in registros:
                previo = registros[uuid]
                hoja_prev, fila_prev = origen_uuid[uuid]
                campos = ("rfc_emisor", "fecha_emision", "monto_total",
                          "efecto_comprobante", "sat_estado")
                diferencias = [c for c in campos if previo[c] != registro[c]]
                if diferencias:
                    incidencia(hoja.title, n_fila, uuid, "duplicado distinto",
                               f"repite {hoja_prev}!{fila_prev} con diferencias en "
                               f"{diferencias}; se conserva la primera")
                else:
                    incidencia(hoja.title, n_fila, uuid, "duplicado",
                               f"repite {hoja_prev}!{fila_prev} (identico)")
                # Si la repeticion trae un estatus y la primera no, se usa.
                if previo["sat_estado"] is None and registro["sat_estado"]:
                    previo["sat_estado"] = registro["sat_estado"]
                    previo["fecha_cancelacion"] = registro["fecha_cancelacion"]
                continue

            registros[uuid] = registro
            origen_uuid[uuid] = (hoja.title, n_fila)

    return {"registros": registros, "incidencias": incidencias, "por_hoja": por_hoja}


# ---------------------------------------------------------------------------
# Escritura
# ---------------------------------------------------------------------------

def insertar(db, registros: list[dict]) -> tuple[int, list[str]]:
    """
    INSERT ... ON CONFLICT (folio_fiscal) DO NOTHING.
    Devuelve (insertadas, folios_ya_existentes).
    """
    from sqlalchemy.dialects.postgresql import insert
    from app.modelos.facturas_recibidas import FacturasRecibidas

    filas = [{k: v for k, v in r.items() if not k.startswith("_")} for r in registros]

    insertadas: set[str] = set()
    for inicio in range(0, len(filas), 500):
        lote = filas[inicio:inicio + 500]
        sentencia = (
            insert(FacturasRecibidas)
            .values(lote)
            .on_conflict_do_nothing(index_elements=["folio_fiscal"])
            .returning(FacturasRecibidas.folio_fiscal)
        )
        insertadas.update(folio for (folio,) in db.execute(sentencia))

    existentes = [f["folio_fiscal"] for f in filas if f["folio_fiscal"] not in insertadas]
    return len(insertadas), existentes


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _imprimir_resumen(resultado: dict) -> None:
    registros = list(resultado["registros"].values())
    incidencias = resultado["incidencias"]

    print("\n" + "=" * 70)
    print("EXCEL DE GASTOS -> FacturasRecibidas")
    print("=" * 70)
    print("\nFilas leidas por hoja:")
    for hoja, n in resultado["por_hoja"].items():
        print(f"  {hoja:<14}{n:>5}")
    print(f"  {'UNICAS':<14}{len(registros):>5}")

    efectos = Counter(r["efecto_comprobante"] or "?" for r in registros)
    estados = Counter(r["sat_estado"] or "(sin estatus -> SOAP)" for r in registros)
    print("\nPor tipo:    " + ", ".join(f"{k}={v}" for k, v in efectos.most_common()))
    print("Por estatus: " + ", ".join(f"{k}={v}" for k, v in estados.most_common()))

    ingresos = sum(
        (r["monto_total"] for r in registros
         if r["efecto_comprobante"] == "I" and r["sat_estado"] != ESTADO_CANCELADO),
        Decimal(0),
    )
    print(f"Total de ingresos (I) no cancelados: ${ingresos:,.2f}")

    con_marca = sum(1 for r in registros if r["_marcas_cliente"])
    print(f"Filas con marca del cliente ('ok'): {con_marca} (no se importan)")

    print("\nProveedores con mas facturas:")
    proveedores = Counter((r["rfc_emisor"], r["nombre_emisor"]) for r in registros)
    for (rfc, nombre), n in proveedores.most_common(8):
        print(f"  {n:>4}  {rfc:<14}{(nombre or '')[:45]}")

    print("\nIncidencias:")
    tipos = Counter(i["tipo"] for i in incidencias)
    if not tipos:
        print("  ninguna")
    for tipo, n in tipos.most_common():
        print(f"  {tipo}: {n}")
    for i in incidencias:
        if i["tipo"] != "duplicado":
            print(f"    [{i['hoja']}!{i['fila']}] {i['uuid'] or '-'}  {i['detalle']}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Importa el Excel de gastos a FacturasRecibidas.")
    parser.add_argument("ruta", help="Ruta al .xlsx")
    parser.add_argument("--apply", action="store_true", dest="aplicar",
                        help="Escribir en la base. Sin esto es dry-run.")
    parser.add_argument("--reporte", help="CSV con todas las incidencias")
    args = parser.parse_args()

    resultado = leer_libro(args.ruta)
    _imprimir_resumen(resultado)

    if args.reporte:
        with open(args.reporte, "w", newline="", encoding="utf-8-sig") as fh:
            escritor = csv.DictWriter(fh, fieldnames=["hoja", "fila", "uuid", "tipo", "detalle"])
            escritor.writeheader()
            escritor.writerows(resultado["incidencias"])
        print(f"\nReporte de incidencias: {args.reporte}")

    from app.modelos import (  # noqa: F401  registra los mapeos
        usuario, factura, estados, configuracion, conceptos,
        complemento_pago, orden_compra, cp_documento_relacionado,
        correo_procesado, cliente, solicitud_sat, facturas_recibidas,
    )
    from app.BaseDeDatos import SessionLocal

    db = SessionLocal()
    try:
        insertadas, existentes = insertar(db, list(resultado["registros"].values()))
        print("\n" + "-" * 70)
        print(f"Nuevas: {insertadas}   Ya existian (se respetan): {len(existentes)}")
        if args.aplicar:
            db.commit()
            print("APLICADO.")
            print("\nSiguiente paso, verificar el estatus de todo lo importado:")
            print("  python -m app.services.verificacion_recibidas_service "
                  "--limite 800 --dias-cubiertos 0 --incluir-canceladas --apply")
        else:
            db.rollback()
            print("DRY-RUN: se inserto y se deshizo. Repite con --apply para guardar.")
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
