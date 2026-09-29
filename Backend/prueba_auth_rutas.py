"""
Prueba las dependencias de autenticacion.

Hasta el 28/09/2026 la API emitia tokens y no los pedia en ninguna ruta.
La mas delicada es POST /facturas-recibidas/sincronizacion, que hace que el
servidor entre al portal del SAT con la e.firma real.

    python3 prueba_auth_rutas.py
"""
import os
import sys
from datetime import timedelta

os.environ.setdefault("DATABASE_URL", "sqlite://")
os.environ.setdefault("SECRET_KEY", "clave-de-prueba")
os.environ.setdefault("ALGORITMO", "HS256")
os.environ.setdefault("TOKEN_ACCESO_MIN_EXPIRACION", "60")
os.environ.setdefault("GMAIL_CLIENT_ID", "x")
os.environ.setdefault("GMAIL_CLIENT_SECRET", "x")
os.environ.setdefault("GMAIL_REFRESH_TOKEN", "x")

from fastapi import APIRouter, Depends, FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

from app.BaseDeDatos import Base, get_db  # noqa: E402
from app.modelos import usuario, estados  # noqa: F401,E402
from app.modelos.usuario import Usuarios  # noqa: E402
from app.core.seguridad import crear_token_acceso  # noqa: E402
from app.core.dependencias import (  # noqa: E402
    ROLES_ADMIN, requiere_roles, usuario_actual,
)

fallos = []


def afirmar(condicion, mensaje):
    print(("  ok   " if condicion else "  FALLA ") + mensaje)
    if not condicion:
        fallos.append(mensaje)


motor = create_engine("sqlite://", connect_args={"check_same_thread": False},
                      poolclass=StaticPool)
Base.metadata.create_all(motor)
db = sessionmaker(bind=motor)()

db.add_all([
    Usuarios(nombre="Jefa", correo="admin@monsort.test",
             password_hash="x", rol="administrador"),
    Usuarios(nombre="Capturista", correo="captura@monsort.test",
             password_hash="x", rol="capturista"),
])
db.commit()


def token(correo, rol="x", minutos=60):
    return crear_token_acceso({"sub": correo, "rol": rol},
                              expires_delta=timedelta(minutes=minutos))


def cabecera(t):
    return {"Authorization": f"Bearer {t}"}


# App de prueba con una ruta abierta, una con sesion y una de admin.
ruta = APIRouter()


@ruta.get("/abierta")
def abierta():
    return {"ok": True}


@ruta.get("/con-sesion")
def con_sesion(u: Usuarios = Depends(usuario_actual)):
    return {"correo": u.correo, "rol": u.rol}


@ruta.post("/solo-admin")
def solo_admin(u: Usuarios = Depends(requiere_roles(*ROLES_ADMIN))):
    return {"correo": u.correo}


app = FastAPI()
app.dependency_overrides[get_db] = lambda: db
app.include_router(ruta)
http = TestClient(app)


print("[1] Sin token")

afirmar(http.get("/abierta").status_code == 200,
        "una ruta sin dependencia sigue abierta")

r = http.get("/con-sesion")
afirmar(r.status_code == 401, f"sin encabezado da 401 ({r.status_code})")
afirmar("Bearer" in r.headers.get("www-authenticate", ""),
        "con WWW-Authenticate, para que el cliente sepa que es de sesion")

r = http.post("/solo-admin")
afirmar(r.status_code == 401, f"la ruta de admin tambien ({r.status_code})")


print("\n[2] Tokens invalidos")

for descripcion, valor in [
    ("basura", "esto-no-es-un-token"),
    ("firmado con otra clave", crear_token_acceso.__globals__["jwt"].encode(
        {"sub": "admin@monsort.test"}, "otra-clave", algorithm="HS256")),
]:
    r = http.get("/con-sesion", headers=cabecera(valor))
    afirmar(r.status_code == 401, f"{descripcion} da 401 ({r.status_code})")

r = http.get("/con-sesion", headers=cabecera(token("admin@monsort.test", minutos=-1)))
afirmar(r.status_code == 401, f"un token vencido da 401 ({r.status_code})")

r = http.get("/con-sesion", headers=cabecera(token("fantasma@monsort.test")))
afirmar(r.status_code == 401,
        f"token bien firmado de un usuario que ya no existe: 401 ({r.status_code})")


print("\n[3] Token bueno")

r = http.get("/con-sesion", headers=cabecera(token("captura@monsort.test")))
afirmar(r.status_code == 200, f"entra ({r.status_code})")
afirmar(r.json()["rol"] == "capturista",
        f"y trae el usuario de la base ({r.json()})")


print("\n[4] Roles")

r = http.post("/solo-admin", headers=cabecera(token("captura@monsort.test")))
afirmar(r.status_code == 403,
        f"un capturista recibe 403, no 401: esta identificado, le falta "
        f"permiso ({r.status_code})")

r = http.post("/solo-admin", headers=cabecera(token("admin@monsort.test")))
afirmar(r.status_code == 200, f"la administradora si pasa ({r.status_code})")

# El rol sale de la BASE, no del token: si alguien se fabrica un token con
# rol=administrador para su propia cuenta, no le sirve de nada.
r = http.post("/solo-admin",
              headers=cabecera(token("captura@monsort.test", rol="administrador")))
afirmar(r.status_code == 403,
        f"un token que se auto-declara administrador NO pasa: el rol se lee "
        f"de la base ({r.status_code})")

db.close()

print()
if fallos:
    print(f"{len(fallos)} PRUEBA(S) FALLIDA(S):")
    for f in fallos:
        print("  -", f)
    sys.exit(1)
print("Todas las pruebas de autenticacion pasaron.")
