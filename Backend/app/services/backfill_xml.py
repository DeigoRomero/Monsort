# Relee los XML almacenados en Facturas.xml_factura y actualiza los campos
# derivados que puedan estar desincronizados (moneda, tipo_cambio, etc.).
#
# Idempotente: correrlo N veces produce el mismo resultado.
# Solo escribe cuando el valor en BD difiere del valor en el XML.
#
# Uso desde backend/:
#     python -m app.services.backfill_xml --dry-run     # solo reporta
#     python -m app.services.backfill_xml               # aplica cambios
#     python -m app.services.backfill_xml --id 5        # una sola factura
#
# Para agregar un campo nuevo al backfill en el futuro, basta con añadirlo
# a CAMPOS_BACKFILL: no hay que tocar la lógica de recorrido ni de commit.

import argparse
import sys
from decimal import Decimal, InvalidOperation
from xml.etree.ElementTree import ParseError

from app.BaseDeDatos import SessionLocal
from app.modelos.factura import Facturas
from app.services.factura_service import extraer_datos_xml


# ─────────────────────────────────────────────────────────────────────────
# Campos a sincronizar desde el XML.
#   clave_xml     → llave del dict que devuelve extraer_datos_xml()
#   atributo      → nombre del atributo en el modelo Facturas
#   conversion    → función que normaliza el valor antes de comparar/guardar
# ─────────────────────────────────────────────────────────────────────────

def _a_decimal(valor):
    if valor is None:
        return None
    try:
        return Decimal(str(valor))
    except (InvalidOperation, ValueError):
        return None


def _a_texto(valor):
    return str(valor).upper() if valor is not None else None


CAMPOS_BACKFILL = [
    ("moneda",      "moneda",      _a_texto),
    ("tipo_cambio", "tipo_cambio", _a_decimal),
]


# ─────────────────────────────────────────────────────────────────────────
# Validación cruzada (capa 3): detecta datos incoherentes
# ─────────────────────────────────────────────────────────────────────────

RANGOS_PLAUSIBLES = {
    "MXN": (Decimal("1"),  Decimal("1")),
    "USD": (Decimal("10"), Decimal("35")),
    "EUR": (Decimal("12"), Decimal("40")),
    "CAD": (Decimal("8"),  Decimal("25")),
}


def validar_coherencia(factura, datos_xml) -> list[str]:
    """Devuelve una lista de advertencias. Vacía = todo coherente."""
    avisos = []

    moneda = (datos_xml.get("moneda") or "MXN").upper()
    tc = _a_decimal(datos_xml.get("tipo_cambio"))

    # 1. Moneda extranjera sin tipo de cambio (CFDI no conforme)
    if moneda not in ("MXN", "XXX") and tc is None:
        avisos.append(
            f"moneda={moneda} sin TipoCambio en el XML (CFDI no conforme al Anexo 20)"
        )

    # 2. Tipo de cambio fuera de rango plausible
    if tc is not None and moneda in RANGOS_PLAUSIBLES:
        minimo, maximo = RANGOS_PLAUSIBLES[moneda]
        if not (minimo <= tc <= maximo):
            avisos.append(
                f"TipoCambio={tc} fuera del rango esperado para {moneda} "
                f"({minimo}–{maximo})"
            )

    # 3. Aritmética: subtotal + iva ≈ total (tolerancia de 1 peso por redondeos)
    sub = _a_decimal(datos_xml.get("subtotal"))
    iva = _a_decimal(datos_xml.get("iva")) or Decimal("0")
    tot = _a_decimal(datos_xml.get("total"))
    if sub is not None and tot is not None:
        diferencia = abs((sub + iva) - tot)
        if diferencia > Decimal("1.00"):
            avisos.append(
                f"subtotal({sub}) + iva({iva}) = {sub + iva} "
                f"no cuadra con total({tot}), diferencia {diferencia}"
            )

    # 4. El UUID del XML debe coincidir con el almacenado
    uuid_xml = datos_xml.get("folio_fiscal")
    if uuid_xml and factura.folio_fiscal and uuid_xml != factura.folio_fiscal:
        avisos.append(
            f"UUID del XML ({uuid_xml}) != UUID en BD ({factura.folio_fiscal})"
        )

    return avisos


# ─────────────────────────────────────────────────────────────────────────
# Motor de backfill
# ─────────────────────────────────────────────────────────────────────────

def procesar_factura_backfill(factura, dry_run: bool) -> dict:
    """
    Procesa una factura. Devuelve un dict con el resultado:
        {"estado": "ok"|"sin_xml"|"error"|"sin_cambios",
         "cambios": [(campo, antes, despues), ...],
         "avisos":  [str, ...],
         "error":   str | None}
    """
    resultado = {"estado": "sin_cambios", "cambios": [], "avisos": [], "error": None}

    if not factura.xml_factura:
        resultado["estado"] = "sin_xml"
        return resultado

    try:
        datos_xml = extraer_datos_xml(factura.xml_factura)
    except ParseError as e:
        resultado["estado"] = "error"
        resultado["error"] = f"XML mal formado: {e}"
        return resultado
    except Exception as e:
        resultado["estado"] = "error"
        resultado["error"] = f"{type(e).__name__}: {e}"
        return resultado

    # Validación cruzada
    resultado["avisos"] = validar_coherencia(factura, datos_xml)

    # Sincronizar campos
    for clave_xml, atributo, conversion in CAMPOS_BACKFILL:
        valor_xml = conversion(datos_xml.get(clave_xml))
        valor_bd = conversion(getattr(factura, atributo))

        if valor_xml is None:
            continue  # el XML no lo trae: no sobrescribir con NULL

        if valor_bd != valor_xml:
            resultado["cambios"].append((atributo, valor_bd, valor_xml))
            if not dry_run:
                setattr(factura, atributo, valor_xml)

    if resultado["cambios"]:
        resultado["estado"] = "ok"

    return resultado


def ejecutar_backfill(dry_run: bool = False, id_factura: int | None = None):
    db = SessionLocal()

    try:
        consulta = db.query(Facturas)
        if id_factura is not None:
            consulta = consulta.filter(Facturas.id_factura == id_factura)
        facturas = consulta.order_by(Facturas.id_factura).all()

        if not facturas:
            print("No se encontraron facturas.")
            return

        modo = "SIMULACIÓN (no se guardan cambios)" if dry_run else "APLICANDO CAMBIOS"
        print(f"\n{'=' * 70}")
        print(f"  BACKFILL DESDE XML — {modo}")
        print(f"  Facturas a revisar: {len(facturas)}")
        print(f"{'=' * 70}\n")

        contadores = {"ok": 0, "sin_cambios": 0, "sin_xml": 0, "error": 0}
        total_avisos = 0

        for f in facturas:
            r = procesar_factura_backfill(f, dry_run)
            contadores[r["estado"]] += 1

            # Solo imprimir filas que tengan algo que reportar
            if r["cambios"] or r["avisos"] or r["estado"] in ("error", "sin_xml"):
                print(f"── Factura {f.id_factura}  ({f.folio_fiscal})")

                if r["estado"] == "sin_xml":
                    print("   [SIN XML] no hay xml_factura almacenado")

                if r["estado"] == "error":
                    print(f"   [ERROR] {r['error']}")

                for campo, antes, despues in r["cambios"]:
                    marca = "→ (simulado)" if dry_run else "→ actualizado"
                    print(f"   {campo}: {antes!r}  →  {despues!r}   {marca}")

                for aviso in r["avisos"]:
                    total_avisos += 1
                    print(f"   [AVISO] {aviso}")

                print()

        if not dry_run:
            db.commit()

        print(f"{'=' * 70}")
        print(f"  Actualizadas:   {contadores['ok']}")
        print(f"  Sin cambios:    {contadores['sin_cambios']}")
        print(f"  Sin XML:        {contadores['sin_xml']}")
        print(f"  Con error:      {contadores['error']}")
        print(f"  Avisos totales: {total_avisos}")
        if dry_run:
            print("\n  SIMULACIÓN: no se guardó nada.")
            print("  Corre sin --dry-run para aplicar los cambios.")
        else:
            print("\n  Cambios guardados en la base de datos.")
        print(f"{'=' * 70}\n")

    except Exception as e:
        db.rollback()
        print(f"\n[FALLO] Se revirtieron todos los cambios: {type(e).__name__}: {e}")
        raise
    finally:
        db.close()


def main():
    parser = argparse.ArgumentParser(
        description="Resincroniza campos de Facturas desde el XML almacenado."
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Simula sin escribir en la base de datos.",
    )
    parser.add_argument(
        "--id", type=int, default=None, metavar="ID_FACTURA",
        help="Procesar una sola factura por su id.",
    )
    args = parser.parse_args()

    ejecutar_backfill(dry_run=args.dry_run, id_factura=args.id)


if __name__ == "__main__":
    main()