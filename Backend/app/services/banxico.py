"""
Consulta el tipo de cambio FIX historico a la API del SIE de Banco de Mexico.

Se usa para rellenar el tipo_cambio de las facturas en USD del historico
que llegaron sin ese dato capturado.

Token: se pide gratis en https://www.banxico.org.mx/SieAPIRest/service/v1/token
Guardalo en .env como BANXICO_TOKEN.

Uso desde codigo:
    from app.services.banxico import obtener_tc_rango
    tabla = obtener_tc_rango(date(2026, 1, 1), date(2026, 8, 31))
    tc = tc_para_factura(tabla, date(2026, 2, 17))

Uso desde consola (prueba rapida):
    python -m app.services.banxico 2026-01-01 2026-08-31
"""

import os
import sys
from datetime import date, datetime, timedelta
from decimal import Decimal

import requests
from dotenv import load_dotenv

load_dotenv()

SERIE_FIX = "SF43718"
URL_BASE = "https://www.banxico.org.mx/SieAPIRest/service/v1/series"
TIMEOUT = 30


class BanxicoError(RuntimeError):
    pass


def _token():
    token = os.getenv("BANXICO_TOKEN")
    if not token:
        raise BanxicoError(
            "Falta BANXICO_TOKEN en .env. "
            "Pidelo en https://www.banxico.org.mx/SieAPIRest/service/v1/token"
        )
    return token


def obtener_tc_rango(desde: date, hasta: date) -> dict[date, Decimal]:
    """
    Devuelve {fecha: tipo_de_cambio} para el rango pedido.

    Banxico solo publica dias habiles: los fines de semana y festivos
    simplemente no vienen en la respuesta. tc_para_factura() se encarga
    de retroceder al dia habil anterior.
    """
    url = f"{URL_BASE}/{SERIE_FIX}/datos/{desde:%Y-%m-%d}/{hasta:%Y-%m-%d}"
    respuesta = requests.get(
        url,
        headers={"Bmx-Token": _token(), "Accept": "application/json"},
        timeout=TIMEOUT,
    )

    if respuesta.status_code == 401:
        raise BanxicoError("Token de Banxico invalido o inhabilitado")
    respuesta.raise_for_status()

    try:
        series = respuesta.json()["bmx"]["series"][0]
    except (KeyError, IndexError, ValueError) as e:
        raise BanxicoError(f"Respuesta inesperada de Banxico: {e}") from e

    tabla = {}
    for registro in series.get("datos", []):
        valor = registro.get("dato")
        if not valor or valor in ("N/E", "N/A"):
            continue
        fecha = datetime.strptime(registro["fecha"], "%d/%m/%Y").date()
        tabla[fecha] = Decimal(valor)

    return tabla


def tc_para_factura(tabla: dict[date, Decimal], fecha_factura: date,
                    max_retroceso: int = 7):
    """
    Tipo de cambio aplicable a un CFDI emitido en fecha_factura.

    El CFDI usa el TC publicado en el DOF el dia habil ANTERIOR a la
    emision, que corresponde al FIX determinado ese dia. Por eso se
    arranca en fecha_factura - 1 y se retrocede hasta encontrar un dia
    habil publicado (fines de semana y festivos no traen dato).
    """
    if not fecha_factura:
        return None

    dia = fecha_factura - timedelta(days=1)
    for _ in range(max_retroceso):
        if dia in tabla:
            return tabla[dia]
        dia -= timedelta(days=1)
    return None


def main():
    if len(sys.argv) < 3:
        print("Uso: python -m app.services.banxico <desde> <hasta>   "
              "(formato YYYY-MM-DD)")
        sys.exit(1)

    desde = date.fromisoformat(sys.argv[1])
    hasta = date.fromisoformat(sys.argv[2])

    tabla = obtener_tc_rango(desde, hasta)
    print(f"{len(tabla)} dias habiles con dato entre {desde} y {hasta}\n")

    for fecha in sorted(tabla)[:10]:
        print(f"  {fecha}  {tabla[fecha]}")
    if len(tabla) > 10:
        print(f"  ... y {len(tabla) - 10} mas")

    valores = list(tabla.values())
    if valores:
        print(f"\n  min {min(valores)}  max {max(valores)}")


if __name__ == "__main__":
    main()