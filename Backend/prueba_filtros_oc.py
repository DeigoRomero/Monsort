"""
Prueba los filtros de GET /ordenes-compra/ (14/10/2026) contra la base del
.env. Solo lee; el PATCH de prueba se deshace al final (rollback).

    python prueba_filtros_oc.py

Comprueba propiedades, no números fijos: sirve con cualquier copia de la base.
"""
from datetime import date, datetime
from decimal import Decimal

from fastapi import FastAPI
from fastapi.testclient import TestClient

from sqlalchemy.orm import Session

from app.BaseDeDatos import engine, get_db
from app.api.rutas.ordenes_compra import router

fallos = []


def afirmar(condicion, mensaje):
    print(("  ok   " if condicion else "  FALLA ") + mensaje)
    if not condicion:
        fallos.append(mensaje)


# Transacción externa: los commit() del PATCH quedan en un savepoint y el
# rollback final deja la base como estaba.
conexion = engine.connect()
transaccion = conexion.begin()
db = Session(bind=conexion, join_transaction_mode="create_savepoint")
app = FastAPI()
app.include_router(router, prefix="/ordenes-compra")
app.dependency_overrides[get_db] = lambda: db
c = TestClient(app)


def listar(**params):
    r = c.get("/ordenes-compra/", params=params)
    assert r.status_code == 200, (r.status_code, r.text)
    return r.json()


try:
    todas = listar()
    print(f"{len(todas)} OCs en la base")
    afirmar(len(todas) > 0, "hay OCs para probar")
    afirmar(all("cliente" in o and "monto" in o and "moneda" in o for o in todas),
            "el listado trae cliente, monto y moneda")

    # con / sin factura se reparten todo
    con = listar(con_factura="true")
    sin = listar(con_factura="false")
    afirmar(len(con) + len(sin) == len(todas), f"con factura ({len(con)}) + sin ({len(sin)}) = todas")
    afirmar(all(o["facturas_asociadas"] > 0 for o in con), "con_factura=true: todas tienen factura")
    afirmar(all(o["facturas_asociadas"] == 0 for o in sin), "con_factura=false: ninguna tiene factura")

    # cliente
    con_cliente = [o for o in todas if o["id_cliente"]]
    if con_cliente:
        cid, nombre = con_cliente[0]["id_cliente"], con_cliente[0]["cliente"]
        por_cliente = listar(id_cliente=cid)
        afirmar(por_cliente and all(o["id_cliente"] == cid for o in por_cliente),
                f"id_cliente={cid}: solo de {nombre}")
        palabra = nombre.split()[0]
        por_texto = listar(q=palabra.lower())
        afirmar(all(o["cliente"] and palabra.lower() in o["cliente"].lower()
                    or palabra.lower() in (o["numero_oc"] or "").lower()
                    or palabra.lower() in (o["nombre_archivo"] or "").lower()
                    for o in por_texto) and len(por_texto) >= len(por_cliente),
                f"q='{palabra.lower()}' busca por nombre de cliente")

    # monto
    con_monto = [o for o in todas if o["monto"] is not None]
    if con_monto:
        m = Decimal(con_monto[0]["monto"])
        exacto = listar(q=f"{m:,.2f}")
        afirmar(any(Decimal(o["monto"]) == m for o in exacto if o["monto"]),
                f"q='{m:,.2f}' encuentra el monto exacto")
        entero = listar(q=str(int(m)))
        afirmar(any(o["monto"] and int(Decimal(o["monto"])) == int(m) for o in entero),
                f"q='{int(m)}' encuentra el monto sin centavos")
        rango = listar(monto_min=str(m), monto_max=str(m))
        afirmar(rango and all(Decimal(o["monto"]) == m for o in rango), "monto_min = monto_max = m")
        mayores = listar(monto_min="10000")
        afirmar(all(Decimal(o["monto"]) >= 10000 for o in mayores), "monto_min=10000")

    # fechas (día completo incluido)
    con_fecha = [o for o in todas if o["fecha_recepcion"]]
    if con_fecha:
        dia = datetime.fromisoformat(con_fecha[0]["fecha_recepcion"]).date()
        ese_dia = listar(fecha_desde=dia.isoformat(), fecha_hasta=dia.isoformat())
        afirmar(any(o["id"] == con_fecha[0]["id"] for o in ese_dia),
                f"desde=hasta={dia}: incluye la OC de ese día")
        afirmar(all(datetime.fromisoformat(o["fecha_recepcion"]).date() == dia for o in ese_dia),
                "solo OCs de ese día")

    # combinación y validaciones
    combo = listar(con_factura="false", monto_min="1")
    afirmar(all(o["facturas_asociadas"] == 0 and Decimal(o["monto"]) >= 1 for o in combo),
            "filtros combinables")
    afirmar(c.get("/ordenes-compra/", params={"fecha_desde": "2026-10-10",
                                               "fecha_hasta": "2026-10-01"}).status_code == 422,
            "desde > hasta -> 422")
    afirmar(c.get("/ordenes-compra/", params={"monto_min": "-5"}).status_code == 422, "monto negativo -> 422")
    afirmar(c.get("/ordenes-compra/", params={"q": "x" * 101}).status_code == 422, "q de 101 caracteres -> 422")
    afirmar(listar(q="%") == listar(q="%") and isinstance(listar(q="'; drop"), list),
            "texto raro en q no rompe la consulta")

    # PATCH parcial (se deshace con rollback)
    oc = todas[0]
    r = c.patch(f"/ordenes-compra/{oc['id']}", json={"monto": "1234.50"})
    afirmar(r.status_code == 200 and r.json()["monto"] == "1234.50"
            and r.json()["numero_oc"] == oc["numero_oc"],
            "PATCH solo monto: no toca el número")
    afirmar(r.json()["moneda"] in ("MXN", "USD"), "PATCH monto deja moneda")
    afirmar(c.patch(f"/ordenes-compra/{oc['id']}", json={}).status_code == 422, "PATCH vacío -> 422")
    afirmar(c.patch(f"/ordenes-compra/{oc['id']}", json={"moneda": "EUR"}).status_code == 422, "moneda EUR -> 422")
    afirmar(c.patch(f"/ordenes-compra/{oc['id']}", json={"numero_oc": None}).status_code == 422,
            "numero_oc null -> 422")
    afirmar(c.patch(f"/ordenes-compra/{oc['id']}", json={"id_cliente": 999999}).status_code == 422,
            "cliente inexistente -> 422")

    # Descartadas (15/10/2026)
    afirmar(not any(o["descartada"] for o in todas), "el listado normal no trae descartadas")
    desc = listar(descartadas="true")
    afirmar(all(o["descartada"] and o["motivo_descarte"] for o in desc),
            f"descartadas=true: {len(desc)} con su motivo")
    afirmar(not any(o["sin_factura_30_dias"] for o in desc), "una descartada no alerta +30 días")
    con_fact = next((o for o in todas if o["facturas_asociadas"] > 0), None)
    if con_fact:
        afirmar(c.patch(f"/ordenes-compra/{con_fact['id']}", json={"descartada": True}).status_code == 409,
                "no se descarta una OC con facturas -> 409")
    libre = next((o for o in todas if o["facturas_asociadas"] == 0), None)
    if libre:
        r = c.patch(f"/ordenes-compra/{libre['id']}", json={"descartada": True})
        afirmar(r.status_code == 200 and r.json()["descartada"] and r.json()["motivo_descarte"],
                "descartar a mano")
        afirmar(all(o["id"] != libre["id"] for o in listar()), "ya no sale en el listado")
        r = c.patch(f"/ordenes-compra/{libre['id']}", json={"descartada": False})
        afirmar(r.status_code == 200 and not r.json()["descartada"]
                and r.json()["confianza_oc"] == "manual", "restaurar la deja como confirmada a mano")
    afirmar(c.patch(f"/ordenes-compra/{oc['id']}", json={"descartada": None}).status_code == 422,
            "descartada null -> 422")

    # Vinculación (15/10/2026): después de reconciliar no queda nada pendiente
    from app.services.factura_service import (
        descartar_ocs_duplicadas, reconciliar, vincular_facturas_con_oc,
    )
    resumen = reconciliar(db)
    afirmar(isinstance(resumen, dict) and "vinculadas" in resumen, f"reconciliar devuelve resumen {resumen}")
    afirmar(vincular_facturas_con_oc(db) == 0 and descartar_ocs_duplicadas(db) == 0,
            "segunda pasada: 0 vínculos y 0 duplicadas nuevas (idempotente)")
finally:
    db.close()
    transaccion.rollback()
    conexion.close()

print("\n" + ("TODO BIEN" if not fallos else f"{len(fallos)} FALLAS"))
raise SystemExit(1 if fallos else 0)
