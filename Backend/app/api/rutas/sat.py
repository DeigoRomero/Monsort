"""
Reconciliación contra el SAT: lo que el SAT dice que Monsort emitió, contra
lo que el sistema tiene en `Facturas`.

Existe por el incidente del 26/09/2026. El cliente notó a las dos semanas que
faltaban pagos; nadie podía notar que faltaban *facturas*, porque no había con
qué comparar. El SAT sí sabe cuántas se emitieron cada mes.

La metadata es el índice barato — su solicitud no consume el límite de por vida
del SAT — y el tipo `cfdi` es la copia cara. Estos endpoints sirven para usar el
índice y gastar una sola solicitud precisa.
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.BaseDeDatos import get_db
from app.esquemas.sat import (
    CoberturaMes, ComprobanteFaltante, BusquedaUuid,
)
from app.services.ingesta_emitidas_service import (
    cobertura_por_mes, faltantes, buscar_uuid,
)

router = APIRouter()


@router.get("/cobertura", response_model=list[CoberturaMes],
            tags=["Reconciliación SAT"])
def obtener_cobertura(
    incluir_canceladas: bool = False,
    db: Session = Depends(get_db),
):
    """
    Mes por mes: cuántas emitidas dice el SAT y cuántas tenemos.

    Lee `metadata_consultada` antes de interpretar `faltantes`: un mes con
    `sat_dice = 0` no significa que no falte nada, significa que no se ha
    pedido metadata de ese mes. Sin metadata no hay comparación posible.

    Las canceladas se excluyen por defecto: no tener una cancelada no es falta.
    """
    return cobertura_por_mes(db, incluir_canceladas=incluir_canceladas)


@router.get("/faltantes", response_model=list[ComprobanteFaltante],
            tags=["Reconciliación SAT"])
def obtener_faltantes(
    mes: str | None = Query(None, description="Filtra por mes, formato AAAA-MM"),
    limite: int = Query(200, ge=1, le=2000),
    incluir_canceladas: bool = False,
    db: Session = Depends(get_db),
):
    """
    Los comprobantes que el SAT registró y no están en la base.

    Cada uno trae su fecha de emisión: con eso se arma el rango exacto de la
    solicitud `cfdi`, en vez de pedir un año entero a ciegas.
    """
    return faltantes(db, mes=mes, limite=limite,
                     incluir_canceladas=incluir_canceladas)


@router.get("/uuid/{folio_fiscal}", response_model=BusquedaUuid,
            tags=["Reconciliación SAT"])
def consultar_uuid(folio_fiscal: str, db: Session = Depends(get_db)):
    """
    Todo lo que se sabe de un UUID, en los tres lugares donde puede estar:
    `Facturas`, la metadata del SAT, y los pagos que lo referencian.

    Es la consulta que resuelve un pago huérfano. El campo `recomendacion`
    dice, en español, qué hacer con él.
    """
    if not folio_fiscal or len(folio_fiscal.strip()) < 10:
        raise HTTPException(status_code=400, detail="Folio fiscal inválido")

    return buscar_uuid(db, folio_fiscal)
