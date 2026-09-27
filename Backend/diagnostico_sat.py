r"""
Diagnostico del frente SAT (Descarga Masiva). Solo LEE: no crea solicitudes
ni toca la base, asi que no gasta cuota.

Uso (en el VPS, desde /srv/monsort/ProyectoMonsort/Backend):

    /srv/monsort/.venv/bin/python diagnostico_sat.py              # todas
    /srv/monsort/.venv/bin/python diagnostico_sat.py --id 50 --id 58
    /srv/monsort/.venv/bin/python diagnostico_sat.py --crudo      # + SOAP

Para cada solicitud que ya tiene IdSolicitud del SAT, vuelve a llamar a
VerificaSolicitudDescarga e imprime TODOS los campos, incluido
CodigoEstadoSolicitud, que el sistema descartaba hasta el 27/09/2026.

Como leer el resultado:

  EstadoSolicitud 1 Aceptada   -> el SAT ni la ha empezado (cola del SAT)
                  2 EnProceso  -> la esta armando
                  3 Terminada  -> hay paquetes (o 0 CFDI)
                  4 Error / 5 Rechazada / 6 Vencida -> terminal
  CodigoEstadoSolicitud: 5000 ok, 5002 limite de por vida, 5003 tope maximo,
                  5004 sin informacion, 5005 duplicada, 5011 limite por dia.
"""

from __future__ import annotations

import argparse
import logging


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    parser.add_argument("--id", type=int, action="append",
                        help="id local de SolicitudesSAT (se puede repetir)")
    parser.add_argument("--crudo", action="store_true",
                        help="imprime tambien la respuesta SOAP completa")
    args = parser.parse_args()

    logging.basicConfig(level=logging.WARNING, format="%(levelname)-8s %(message)s")
    if args.crudo:
        logging.getLogger("cfdiclient").setLevel(logging.DEBUG)

    from app.modelos import (  # noqa: F401  registra los mapeos
        usuario, factura, estados, configuracion, conceptos,
        complemento_pago, orden_compra, cp_documento_relacionado,
        correo_procesado, cliente, solicitud_sat, facturas_recibidas,
    )
    from app.BaseDeDatos import SessionLocal
    from app.modelos.solicitud_sat import SolicitudesSAT
    from app.services.sat_descarga_client_real import construir_cliente_sat
    from cfdiclient import VerificaSolicitudDescarga

    cliente = construir_cliente_sat()
    if cliente is None:
        raise SystemExit("Sin e.firma configurada: nada que diagnosticar.")
    print(f"Cliente SAT REAL (RFC {cliente.rfc})\n")

    db = SessionLocal()
    try:
        consulta = db.query(SolicitudesSAT).filter(
            SolicitudesSAT.id_solicitud_sat.isnot(None)
        )
        if args.id:
            consulta = consulta.filter(SolicitudesSAT.id.in_(args.id))
        solicitudes = consulta.order_by(SolicitudesSAT.id).all()

        token = cliente.autenticar()
        for s in solicitudes:
            servicio = VerificaSolicitudDescarga(cliente.fiel, timeout=cliente.timeout)
            try:
                r = servicio.verificar_descarga(token, cliente.rfc, s.id_solicitud_sat)
            except Exception as error:  # noqa: BLE001
                r = {"error": str(error)}

            print(f"#{s.id}  {s.fecha_inicial}..{s.fecha_final}  "
                  f"{s.tipo_solicitud}/{s.tipo_comprobante}  local={s.estado}  "
                  f"creada={s.fecha_creacion:%Y-%m-%d %H:%M}Z  "
                  f"intentos={s.intentos_verificacion}")
            print(f"     IdSolicitud          {s.id_solicitud_sat}")
            for clave in ("cod_estatus", "estado_solicitud",
                          "codigo_estado_solicitud", "numero_cfdis",
                          "mensaje", "paquetes", "error"):
                if clave in r:
                    print(f"     {clave:<21}{r[clave]}")
            print()
    finally:
        db.close()


if __name__ == "__main__":
    main()
