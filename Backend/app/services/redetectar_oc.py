"""
Vuelve a detectar el número de las OCs ya guardadas con el detector nuevo, y
recupera OCs que llegaron en el CUERPO de correos ya procesados.

Por defecto NO escribe nada (muestra lo que cambiaría). Uso, desde Backend/:

    # 1. Ver qué cambiaría en las OCs existentes
    python -m app.services.redetectar_oc

    # 2. Aplicarlo
    python -m app.services.redetectar_oc --aplicar

    # 3. Buscar OCs en el cuerpo de correos sin adjunto (Regal Rexnord /
    #    SciQuest, Coupa...). Usa la API de Gmail: solo lectura.
    python -m app.services.redetectar_oc --buscar-cuerpo --desde 2026-09-01 --hasta 2026-09-30
    python -m app.services.redetectar_oc --buscar-cuerpo --desde 2026-09-01 --hasta 2026-09-30 --aplicar

Reglas al aplicar:
  - Si el usuario NO había corregido el número (numero_oc == numero_oc_detectado),
    se reemplaza por el nuevo.
  - Si el usuario SÍ lo corrigió a mano, se respeta su valor; solo se anota
    la detección nueva en numero_oc_detectado y confianza_oc = 'manual'.
  - Los PDFs que el detector ya no considera OC (cotizaciones, recibos) solo
    se LISTAN para revisión; no se borra nada.
  - Los vínculos factura→OC existentes no se tocan (van por id). Al final
    se corre reconciliar() para enlazar lo que ahora sí coincide.
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
from datetime import date, timedelta

from sqlalchemy.orm import load_only

from app.BaseDeDatos import SessionLocal
from app.modelos.correo_procesado import CorreosProcesados
from app.modelos.orden_compra import OrdenesCompra
from app.services.detector_oc import detectar_numero_oc, es_documento_oc

logger = logging.getLogger(__name__)


def redetectar_existentes(db, aplicar: bool) -> dict:
    from app.services.factura_service import extraer_texto_y_palabras_pdf

    resumen = {"revisadas": 0, "cambian": 0, "respetadas": 0, "no_parecen_oc": 0, "iguales": 0}
    ocs = db.query(OrdenesCompra).order_by(OrdenesCompra.id).all()
    print(f"{'id':>5}  {'actual':<18} {'nuevo':<18} {'conf.':<8} archivo")
    for oc in ocs:
        if not oc.archivo:
            continue
        resumen["revisadas"] += 1
        try:
            texto, palabras = extraer_texto_y_palabras_pdf(oc.archivo)
        except Exception:
            texto, palabras = "", []

        if not es_documento_oc(texto, oc.nombre_archivo):
            resumen["no_parecen_oc"] += 1
            print(f"{oc.id:>5}  {str(oc.numero_oc):<18} {'(no parece OC)':<18} {'':<8} {oc.nombre_archivo}")
            continue

        r = detectar_numero_oc(texto_pdf=texto, palabras_pdf=palabras,
                               nombre_archivo=oc.nombre_archivo)
        if r.numero == oc.numero_oc:
            resumen["iguales"] += 1
            if aplicar and not oc.confianza_oc:
                oc.confianza_oc = r.confianza
                oc.numero_oc_detectado = r.numero
            continue

        corregida_a_mano = (oc.numero_oc or "") != (oc.numero_oc_detectado or "")
        marca = "manual" if corregida_a_mano else r.confianza
        print(f"{oc.id:>5}  {str(oc.numero_oc):<18} {str(r.numero):<18} {marca:<8} {oc.nombre_archivo}"
              + ("   <- corregida a mano: se respeta" if corregida_a_mano else ""))
        if corregida_a_mano:
            resumen["respetadas"] += 1
        else:
            resumen["cambian"] += 1
        if aplicar:
            oc.numero_oc_detectado = r.numero
            if corregida_a_mano:
                oc.confianza_oc = "manual"
            elif r.numero:
                oc.numero_oc = r.numero
                oc.confianza_oc = r.confianza
    return resumen


def redetectar_facturas(db, aplicar: bool) -> dict:
    """
    Mismo criterio para el número de OC de las FACTURAS (sale del concepto
    del XML). Casos reales: "pipa de agua de 10000 litros. #PO-VSM-14851"
    quedaba con OC 10000; "ORDEN DE COMPRA: BOS-507-91753" con 91753.
    Solo se cambian las que nadie corrigió a mano.
    """
    from app.modelos.factura import Facturas
    from app.services.factura_service import extraer_datos_xml

    resumen = {"facturas_revisadas": 0, "facturas_cambian": 0}
    filas = db.query(Facturas).options(load_only(
        Facturas.id_factura, Facturas.folio_interno, Facturas.numero_oc,
        Facturas.numero_oc_detectado, Facturas.xml_factura,
    )).filter(Facturas.xml_factura.isnot(None)).order_by(Facturas.id_factura).all()
    print(f"\n{'factura':>7}  {'actual':<18} nuevo")
    for f in filas:
        resumen["facturas_revisadas"] += 1
        try:
            nuevo = extraer_datos_xml(f.xml_factura)["numero_oc"]
        except Exception:
            continue
        if not nuevo or nuevo == f.numero_oc:
            continue
        if (f.numero_oc or "") != (f.numero_oc_detectado or ""):
            continue   # corregida a mano
        resumen["facturas_cambian"] += 1
        print(f"{f.id_factura:>7}  {str(f.numero_oc):<18} {nuevo}   ({f.folio_interno})")
        if aplicar:
            f.numero_oc = nuevo
            f.numero_oc_detectado = nuevo
    return resumen


def buscar_en_cuerpo(db, desde: date, hasta: date, aplicar: bool) -> dict:
    from app.services.backfill_gmail import construir_query, listar_mensajes
    from app.services.factura_service import procesar_oc_en_cuerpo
    from app.services.gmail_service import extraer_correo, obtener_servicio_gmail

    servicio = obtener_servicio_gmail(db)
    filtro = ('-has:attachment (subject:PO OR subject:"purchase order" OR '
              'subject:"orden de compra" OR subject:OC OR subject:pedido)')
    ids = listar_mensajes(servicio, construir_query(desde, hasta, filtro))
    resumen = {"encontrados": len(ids), "ocs_nuevas": 0}
    for mensaje_id in ids:
        correo = extraer_correo(servicio, mensaje_id)
        with db.begin_nested():
            creada = procesar_oc_en_cuerpo(correo["asunto"], correo["cuerpo"],
                                           correo["remitente"], mensaje_id, db)
        if creada:
            resumen["ocs_nuevas"] += 1
            print(f"  OC en el cuerpo: {correo['asunto'][:80]}")
            if not db.query(CorreosProcesados).filter(
                CorreosProcesados.message_id == mensaje_id
            ).first():
                db.add(CorreosProcesados(message_id=mensaje_id, tipo_correo="ordenes:1"))
        time.sleep(0.5)
    return resumen


def _fecha(texto: str) -> date:
    return date.fromisoformat(texto)


def main():
    logging.basicConfig(level=logging.WARNING)
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--aplicar", action="store_true", help="escribir los cambios (por defecto solo muestra)")
    p.add_argument("--buscar-cuerpo", action="store_true", help="buscar OCs en el cuerpo de correos sin adjunto")
    p.add_argument("--desde", type=_fecha, default=date.today() - timedelta(days=30))
    p.add_argument("--hasta", type=_fecha, default=date.today())
    args = p.parse_args()

    db = SessionLocal()
    try:
        if args.buscar_cuerpo:
            resumen = buscar_en_cuerpo(db, args.desde, args.hasta, args.aplicar)
        else:
            resumen = redetectar_existentes(db, args.aplicar)
            resumen.update(redetectar_facturas(db, args.aplicar))

        if args.aplicar:
            db.commit()
            from app.services.factura_service import reconciliar
            reconciliar(db)
            print("\nCambios aplicados y reconciliado.")
        else:
            db.rollback()
            print("\nSolo vista previa. Agrega --aplicar para guardar.")
        print(resumen)
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
