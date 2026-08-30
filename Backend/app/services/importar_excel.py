"""
Fase 2 de la migracion del historico: inserta las facturas del Excel.

Corre en seco por defecto. Solo escribe con --apply.

    python -m app.services.importar_excel D:\\GENERAL_2026.xlsx
    python -m app.services.importar_excel D:\\GENERAL_2026.xlsx --correcciones correcciones_cliente.csv
    python -m app.services.importar_excel D:\\GENERAL_2026.xlsx --correcciones correcciones_cliente.csv --banxico --apply

Alcance de esta pasada:
  - Solo facturas (tipo_documento == 'factura'). Los complementos de pago
    del Excel no entran: no traen DoctoRelacionado, asi que no podrian
    ligarse a nada. La fecha de pago ya viene en la propia factura.
  - No se crean registros en OrdenesCompra. El numero de OC se guarda
    como texto en la factura; sin archivo ni fecha de recepcion, una OC
    vacia solo ensuciaria la alerta de "OC sin factura +30 dias".
  - Estado Historico, o Cancelada si el Excel la marcaba como tal.
  - origen = 'excel' en todas.
"""

import csv
import sys
from collections import Counter, defaultdict

from app.BaseDeDatos import SessionLocal
from app.modelos.factura import Facturas
from app.modelos.cliente import Cliente
from app.services.usuario_service import obtener_usuario_sistema, obtener_estado
from app.services.factura_service import resolver_cliente
from app.services.analizar_excel import (
    leer_excel, consolidar_rfcs, aplicar_consolidacion,
    calcular_tc_por_mes, aplicar_tc_estimado, aplicar_tc_banxico,
)

ORIGEN = "excel"
ESTADO_HISTORICO = "Histórico"
ESTADO_CANCELADA = "Cancelada"


# ── Correcciones del cliente ────────────────────────────────────────────

def cargar_correcciones(ruta):
    """
    Lee el CSV de parches (folio_fiscal, campo, valor).

    Las correcciones se aplican encima de lo que sale del Excel en vez de
    editarse en el archivo, para no arrastrar el dano que Excel hace al
    guardar un CSV (los numeros de OC pierden el cero inicial).
    """
    if not ruta:
        return {}
    parches = defaultdict(dict)
    with open(ruta, encoding="utf-8-sig") as fh:
        for fila in csv.DictReader(fh):
            uuid = fila["folio_fiscal"].strip().upper()
            parches[uuid][fila["campo"].strip()] = fila["valor"].strip()
    return dict(parches)


def aplicar_correcciones(filas, parches):
    aplicados = 0
    sin_destino = set(parches)
    for f in filas:
        cambios = parches.get(f["folio_fiscal"])
        if not cambios:
            continue
        sin_destino.discard(f["folio_fiscal"])
        for campo, valor in cambios.items():
            if campo in f:
                f[campo] = valor or None
                aplicados += 1
    return aplicados, sin_destino


def reclasificar(filas):
    """El folio interno corregido cambia el tipo de documento."""
    from app.services.analizar_excel import clasificar_documento
    for f in filas:
        f["tipo_documento"] = clasificar_documento(
            f["folio_interno"], f["total"]
        )


# ── Validacion ──────────────────────────────────────────────────────────

def validar(fila):
    """Devuelve el motivo de rechazo, o None si la fila es importable."""
    if fila["tipo_documento"] != "factura":
        return f"no es factura ({fila['tipo_documento']})"
    if not fila["fecha"]:
        return "sin fecha de emision (columna NOT NULL)"
    if not fila["rfc"]:
        return "sin RFC"
    return None


# ── Importacion ─────────────────────────────────────────────────────────

def importar(db, filas, dry_run=True):
    usuario = obtener_usuario_sistema(db)

    estado_historico = obtener_estado(db, ESTADO_HISTORICO)
    estado_cancelada = obtener_estado(db, ESTADO_CANCELADA)
    if not estado_historico:
        raise ValueError(
            f"Falta el estado '{ESTADO_HISTORICO}' en la tabla Estados. "
            "Corre primero la migracion que lo inserta."
        )

    resumen = {
        "insertadas": 0,
        "duplicadas": 0,
        "rechazadas": 0,
        "clientes_nuevos": 0,
        "por_estado": Counter(),
        "por_cliente": Counter(),
    }
    rechazos = []
    clientes_antes = db.query(Cliente).count()

    for fila in filas:
        motivo = validar(fila)
        if motivo:
            resumen["rechazadas"] += 1
            rechazos.append((fila, motivo))
            continue

        existente = db.query(Facturas).filter(
            Facturas.folio_fiscal == fila["folio_fiscal"]
        ).first()
        if existente:
            resumen["duplicadas"] += 1
            continue

        cliente = resolver_cliente(db, fila["rfc"], fila["cliente"] or fila["rfc"])

        estado = estado_cancelada if (fila["cancelada"] and estado_cancelada) \
            else estado_historico

        db.add(Facturas(
            folio_fiscal=fila["folio_fiscal"],
            folio_interno=fila["folio_interno"],
            rfc=fila["rfc"],
            cliente=fila["cliente"],
            fecha=fila["fecha"],
            numero_oc=fila["numero_oc"],
            numero_oc_detectado=fila["numero_oc"],
            moneda=fila["moneda"],
            tipo_cambio=fila["tipo_cambio"],
            tipo_cambio_fuente=fila.get("tipo_cambio_fuente"),
            subtotal=fila["subtotal"],
            iva=fila["iva"],
            total=fila["total"],
            fecha_liquidacion=fila["fecha_liquidacion"],
            # fecha_validacion se deja vacia: el Excel no la registra y de
            # ella depende el calculo de la fecha limite de pago
            message_id=None,
            id_usuario=usuario.id_usuario,
            id_estado=estado.id_estado,
            id_cliente=cliente.id if cliente else None,
            origen=ORIGEN,
        ))
        db.flush()

        resumen["insertadas"] += 1
        resumen["por_estado"][estado.nombre_estado] += 1
        resumen["por_cliente"][fila["cliente"] or fila["rfc"]] += 1

    resumen["clientes_nuevos"] = db.query(Cliente).count() - clientes_antes

    if dry_run:
        db.rollback()
    else:
        db.commit()

    return resumen, rechazos


# ── Reporte ─────────────────────────────────────────────────────────────

def reportar(resumen, rechazos, dry_run):
    modo = "SIMULACION (no se guardo nada)" if dry_run else "APLICADO"
    print()
    print("=" * 68)
    print(f"IMPORTACION DEL HISTORICO  —  {modo}")
    print("=" * 68)
    print(f"  Facturas insertadas : {resumen['insertadas']}")
    print(f"  Ya existian         : {resumen['duplicadas']}")
    print(f"  Rechazadas          : {resumen['rechazadas']}")
    print(f"  Clientes nuevos     : {resumen['clientes_nuevos']}")

    print("\n  Por estado:")
    for estado, n in resumen["por_estado"].most_common():
        print(f"    {estado:<26} {n:>4}")

    print("\n  Por cliente (top 10):")
    for cliente, n in resumen["por_cliente"].most_common(10):
        print(f"    {cliente[:40]:<42} {n:>4}")

    motivos = Counter(m for _, m in rechazos)
    if motivos:
        print("\n  Motivos de rechazo:")
        for motivo, n in motivos.most_common():
            print(f"    {n:>4}  {motivo}")

    detalle = [(f, m) for f, m in rechazos if "no es factura" not in m]
    if detalle:
        print("\n  Rechazos que no son complementos:")
        for f, m in detalle:
            print(f"    {f['hoja']:<12} fila {f['fila_excel']:<5} "
                  f"{str(f['folio_interno']):<12} {m}")

    if dry_run:
        print("\n  Nada se escribio. Vuelve a correr con --apply para importar.")


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    ruta = sys.argv[1]
    dry_run = "--apply" not in sys.argv

    correcciones = None
    if "--correcciones" in sys.argv:
        correcciones = sys.argv[sys.argv.index("--correcciones") + 1]

    filas, _, _, orden_hojas = leer_excel(ruta)
    mapa, _ = consolidar_rfcs(filas)
    aplicar_consolidacion(filas, mapa)

    if correcciones:
        parches = cargar_correcciones(correcciones)
        n, huerfanos = aplicar_correcciones(filas, parches)
        reclasificar(filas)
        print(f"Correcciones aplicadas: {n}")
        if huerfanos:
            print(f"  {len(huerfanos)} correcciones sin factura correspondiente")

    if "--banxico" in sys.argv:
        try:
            n, sin_dato = aplicar_tc_banxico(filas)
            print(f"Banxico: {n} tipos de cambio obtenidos"
                  + (f", {sin_dato} sin dato" if sin_dato else ""))
        except Exception as e:
            print(f"Banxico no disponible ({e}); se usara la mediana mensual.")

    aplicar_tc_estimado(filas, calcular_tc_por_mes(filas, orden_hojas))

    db = SessionLocal()
    try:
        resumen, rechazos = importar(db, filas, dry_run)
        reportar(resumen, rechazos, dry_run)
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    main()