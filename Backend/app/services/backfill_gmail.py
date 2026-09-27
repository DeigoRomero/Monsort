"""
Lectura hacia atras del buzon, por rango de fechas.

Por que existe
--------------
procesar_correos_nuevos() avanza con history.list desde un historyId
guardado: lee lo NUEVO. Nunca mira hacia atras, y Gmail solo conserva el
historial alrededor de una semana. Por eso el sistema no tiene complementos
anteriores a septiembre de 2026: no los descarto, jamas abrio ese correo. La
carga inicial del historico vino de un Excel que traia facturas y ningun CP.

Esto pide los mensajes por rango de fechas con messages.list y los mete por
la misma tuberia (extraer_adjuntos -> procesar_correo), asi que el parser, el
dedupe y la reconciliacion son exactamente los del flujo normal.

Es seguro repetirlo: el dedupe real es folio_fiscal / uuid_cp /
hash_archivo, y ademas se salta los message_id que ya estan en
CorreosProcesados.

NO toca gmail_history_id. Ese valor es la posicion del flujo incremental;
moverlo desde aqui haria que el ciclo normal se saltara correos nuevos o
reprocesara semanas enteras.

Uso
---
    cd /srv/monsort/ProyectoMonsort/Backend
    /srv/monsort/.venv/bin/python -m app.services.backfill_gmail \\
        --desde 2026-08-01 --hasta 2026-08-31

    # Primero, sin bajar nada, para saber cuantos correos hay:
    ... --desde 2026-08-01 --hasta 2026-08-31 --contar

Un mes por corrida. La avalancha de llamadas de golpe es justo lo que reventó
la cuota de Gmail en septiembre.
"""
import argparse
import logging
import time
from datetime import date, datetime, timedelta

from googleapiclient.errors import HttpError
from google.auth.exceptions import RefreshError

from app.modelos.correo_procesado import CorreosProcesados
from app.services.gmail_service import obtener_servicio_gmail, _con_reintentos
from app.services.usuario_service import obtener_usuario_sistema

logger = logging.getLogger(__name__)

# Gmail cobra cuota por llamada. El ciclo normal procesa unos pocos correos;
# aqui pueden ser cientos seguidos, asi que el espaciado es mas generoso.
PAUSA_ENTRE_CORREOS = 1.0
TAMANO_PAGINA = 100

# has:attachment descarta de entrada los ~1,100 correos "desconocido" que el
# pipeline abria para nada: sin adjunto no hay XML ni PDF que guardar, y cada
# uno costaba una llamada. Se puede sobreescribir con --query.
QUERY_POR_DEFECTO = "has:attachment"

# Para no armar un IN con miles de valores.
LOTE_DEDUPE = 500


def _formato_gmail(dia: date) -> str:
    """Gmail usa AAAA/MM/DD en after: y before:."""
    return dia.strftime("%Y/%m/%d")


def construir_query(desde: date, hasta: date, extra: str) -> str:
    """
    after: es inclusivo y before: es exclusivo, y ambos trabajan por dia
    completo en la zona horaria de la cuenta. Para incluir el dia `hasta` hay
    que pedir before: el dia siguiente; sin esto se pierde siempre el ultimo
    dia del rango, que es un error que no se nota hasta que alguien cuadra un
    mes contra el SAT.
    """
    partes = [
        f"after:{_formato_gmail(desde)}",
        f"before:{_formato_gmail(hasta + timedelta(days=1))}",
    ]
    if extra:
        partes.append(extra)
    return " ".join(partes)


def listar_mensajes(servicio, query: str, incluir_spam: bool = False) -> list[str]:
    """Todos los ids del rango, paginando. Solo llamadas list: no baja nada."""
    ids: list[str] = []
    token = None

    while True:
        kwargs = {
            "userId": "me",
            "q": query,
            "maxResults": TAMANO_PAGINA,
            "includeSpamTrash": incluir_spam,
        }
        if token:
            kwargs["pageToken"] = token

        respuesta = _con_reintentos(
            lambda k=dict(kwargs): servicio.users().messages().list(**k).execute(),
            f"listar mensajes ({query})",
        )

        for mensaje in respuesta.get("messages", []):
            if mensaje.get("id"):
                ids.append(mensaje["id"])

        token = respuesta.get("nextPageToken")
        if not token:
            break

    # dict.fromkeys conserva el orden y quita repetidos.
    return list(dict.fromkeys(ids))


def _ya_procesados(db, ids: list[str]) -> set[str]:
    """Los message_id que ya estan en CorreosProcesados, en pocas consultas."""
    encontrados: set[str] = set()
    for inicio in range(0, len(ids), LOTE_DEDUPE):
        lote = ids[inicio:inicio + LOTE_DEDUPE]
        filas = db.query(CorreosProcesados.message_id).filter(
            CorreosProcesados.message_id.in_(lote)
        ).all()
        encontrados.update(f[0] for f in filas)
    return encontrados


def backfill(db, desde: date, hasta: date, limite: int | None = None,
             pausa: float = PAUSA_ENTRE_CORREOS, query_extra: str = QUERY_POR_DEFECTO,
             solo_contar: bool = False, incluir_spam: bool = False) -> dict:
    """
    Lee el buzon entre dos fechas y procesa lo que no se haya procesado.

    Devuelve un reporte con lo que entro, pensado para reportarle avance al
    cliente por mes.
    """
    # Import local: factura_service importa de gmail_service y este modulo lo
    # llama a el. A nivel de modulo seria un ciclo.
    from app.services.factura_service import (
        _procesar_un_correo, _registrar_fallo, describir_resumen, reconciliar,
    )

    reporte = {
        "desde": desde.isoformat(),
        "hasta": hasta.isoformat(),
        "encontrados": 0,
        "ya_estaban": 0,
        "intentados": 0,
        "procesados": 0,
        "fallidos": 0,
        "documentos": {"facturas": 0, "complementos": 0, "ordenes": 0},
        "pendientes_del_rango": 0,
        "detalle": [],
        "error": None,
    }

    try:
        servicio = obtener_servicio_gmail(db)
    except RefreshError as e:
        logger.error("Credenciales de Gmail invalidas (%s). "
                     "Reautoriza en /auth/gmail/iniciar", e)
        reporte["error"] = "credenciales_invalidas"
        return reporte
    except RuntimeError as e:
        logger.error("Gmail no configurado: %s", e)
        reporte["error"] = "gmail_no_configurado"
        return reporte

    query = construir_query(desde, hasta, query_extra)
    logger.info("Buscando en Gmail: %s", query)

    try:
        ids = listar_mensajes(servicio, query, incluir_spam=incluir_spam)
    except HttpError as e:
        logger.error("Gmail rechazo la busqueda (%s): %s", e.resp.status, e)
        reporte["error"] = f"http_{e.resp.status}"
        return reporte

    reporte["encontrados"] = len(ids)

    vistos = _ya_procesados(db, ids)
    pendientes = [i for i in ids if i not in vistos]
    reporte["ya_estaban"] = len(ids) - len(pendientes)

    logger.info(
        "%s a %s: %d correo(s) en el buzon, %d ya procesado(s), %d por procesar",
        desde, hasta, len(ids), reporte["ya_estaban"], len(pendientes),
    )

    if solo_contar:
        reporte["pendientes_del_rango"] = len(pendientes)
        return reporte

    por_procesar = pendientes[:limite] if limite else pendientes
    reporte["pendientes_del_rango"] = len(pendientes) - len(por_procesar)

    usuario_sistema = obtener_usuario_sistema(db)

    for numero, mensaje_id in enumerate(por_procesar, start=1):
        reporte["intentados"] += 1
        try:
            resumen = _procesar_un_correo(db, servicio, mensaje_id, usuario_sistema)
            reporte["procesados"] += 1
            for clave in reporte["documentos"]:
                reporte["documentos"][clave] += resumen.get(clave, 0)

            # Solo lo que trajo algo: un listado de 400 lineas de
            # "desconocido" entierra lo que importa.
            if any(resumen.get(c) for c in reporte["documentos"]):
                reporte["detalle"].append({
                    "message_id": mensaje_id,
                    "resultado": describir_resumen(resumen),
                })
        except Exception as e:
            db.rollback()
            _registrar_fallo(db, mensaje_id, e)
            db.commit()
            reporte["fallidos"] += 1
            logger.warning("Correo %s no procesado: %s", mensaje_id, e)

        if numero % 25 == 0:
            logger.info(
                "  avance: %d/%d (%d con documentos, %d fallidos)",
                numero, len(por_procesar), reporte["procesados"], reporte["fallidos"],
            )

        # Espaciar: la cuota de Gmail es por minuto, y una corrida de cientos
        # de correos sin pausa es exactamente lo que tiro 1,702 mensajes en
        # septiembre de 2026.
        if pausa:
            time.sleep(pausa)

    # Los CP viejos que acaban de entrar necesitan pegarse a sus facturas, que
    # en su mayoria ya estaban desde la importacion del Excel.
    try:
        reconciliar(db)
    except Exception:
        db.rollback()
        logger.exception("Error en la reconciliacion posterior al backfill")

    logger.info(
        "Backfill %s a %s: %d procesado(s), %d fallido(s). "
        "Facturas %d, complementos %d, ordenes %d. Quedan %d del rango.",
        desde, hasta, reporte["procesados"], reporte["fallidos"],
        reporte["documentos"]["facturas"], reporte["documentos"]["complementos"],
        reporte["documentos"]["ordenes"], reporte["pendientes_del_rango"],
    )

    return reporte


# ---------------------------------------------------------------- CLI

def _fecha(texto: str) -> date:
    try:
        return datetime.strptime(texto, "%Y-%m-%d").date()
    except ValueError:
        raise argparse.ArgumentTypeError(
            f"'{texto}' no es una fecha AAAA-MM-DD"
        )


def main():
    parser = argparse.ArgumentParser(
        description="Lee el buzon hacia atras por rango de fechas.",
    )
    parser.add_argument("--desde", type=_fecha, required=True,
                        help="AAAA-MM-DD, inclusivo")
    parser.add_argument("--hasta", type=_fecha, required=True,
                        help="AAAA-MM-DD, inclusivo")
    parser.add_argument("--contar", action="store_true",
                        help="Solo cuenta cuantos correos hay; no baja nada")
    parser.add_argument("--limite", type=int, default=None,
                        help="Maximo de correos a procesar en esta corrida")
    parser.add_argument("--pausa", type=float, default=PAUSA_ENTRE_CORREOS,
                        help=f"Segundos entre correos (default {PAUSA_ENTRE_CORREOS})")
    parser.add_argument("--query", default=QUERY_POR_DEFECTO,
                        help=f"Filtro extra de Gmail (default '{QUERY_POR_DEFECTO}'; "
                             "cadena vacia para no filtrar)")
    parser.add_argument("--incluir-spam", action="store_true",
                        help="Incluye spam y papelera")
    args = parser.parse_args()

    if args.hasta < args.desde:
        parser.error("--hasta es anterior a --desde")

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
    )

    from app.BaseDeDatos import SessionLocal
    db = SessionLocal()
    try:
        reporte = backfill(
            db, args.desde, args.hasta,
            limite=args.limite, pausa=args.pausa,
            query_extra=args.query, solo_contar=args.contar,
            incluir_spam=args.incluir_spam,
        )
    finally:
        db.close()

    print()
    print(f"  Rango            : {reporte['desde']} a {reporte['hasta']}")
    print(f"  Correos en Gmail : {reporte['encontrados']}")
    print(f"  Ya procesados    : {reporte['ya_estaban']}")
    if reporte["error"]:
        print(f"  ERROR            : {reporte['error']}")
        return
    if args.contar:
        print(f"  Por procesar     : {reporte['pendientes_del_rango']}")
        return
    print(f"  Procesados ahora : {reporte['procesados']}")
    print(f"  Fallidos         : {reporte['fallidos']}")
    print(f"  Facturas         : {reporte['documentos']['facturas']}")
    print(f"  Complementos     : {reporte['documentos']['complementos']}")
    print(f"  Ordenes de compra: {reporte['documentos']['ordenes']}")
    if reporte["pendientes_del_rango"]:
        print(f"  Quedan del rango : {reporte['pendientes_del_rango']} "
              "(vuelve a correrlo para seguir)")


if __name__ == "__main__":
    main()
