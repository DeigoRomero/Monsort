"""
Pruebas de seguridad de la API completa (30/09/2026).

Corre contra la base de DATABASE_URL (Postgres de desarrollo): crea usuarios
de prueba con correo @prueba-seguridad.test y los borra al final.

    python prueba_seguridad.py
"""
import os
import re
import sys
import time
from datetime import timedelta

os.environ.setdefault("ENTORNO", "desarrollo")

from fastapi.testclient import TestClient  # noqa: E402
from jose import jwt  # noqa: E402

from main import aplicacion  # noqa: E402
from app.BaseDeDatos import SessionLocal  # noqa: E402
from app.core.config import settings  # noqa: E402
from app.core.seguridad import (  # noqa: E402
    NOMBRE_COOKIE_REFRESH, crear_estado_oauth, crear_token_acceso, hash_password,
    limitador_login_ip, limitador_refresh_ip,
)
from app.modelos.orden_compra import OrdenesCompra  # noqa: E402
from app.modelos.usuario import Usuarios  # noqa: E402

fallos = []
DOMINIO = "@prueba-seguridad.example.com"
PASS = "Correcta-Larga-2026!"


def afirmar(cond, msg):
    print(("  ok    " if cond else "  FALLA ") + msg)
    if not cond:
        fallos.append(msg)


db = SessionLocal()


def limpiar():
    db.query(Usuarios).filter(Usuarios.correo.like(f"%{DOMINIO}")).delete(synchronize_session=False)
    db.query(OrdenesCompra).filter(OrdenesCompra.message_id == "prueba-seguridad").delete()
    db.commit()


limpiar()
admin = Usuarios(nombre="Admin", correo=f"admin{DOMINIO}", password_hash=hash_password(PASS),
                 rol="administrador", activo=True, sesion_version=0, intentos_fallidos=0)
empleado = Usuarios(nombre="Emp", correo=f"emp{DOMINIO}", password_hash=hash_password(PASS),
                    rol="empleado", activo=True, sesion_version=0, intentos_fallidos=0)
inactivo = Usuarios(nombre="Baja", correo=f"baja{DOMINIO}", password_hash=hash_password(PASS),
                    rol="empleado", activo=False, sesion_version=0, intentos_fallidos=0)
db.add_all([admin, empleado, inactivo])
db.commit()

cliente = TestClient(aplicacion)
XRW = {"X-Requested-With": "monsort"}


def bearer(tok):
    return {"Authorization": f"Bearer {tok}"}


def login(correo, password=PASS, c=None):
    return (c or cliente).post("/auth/login", json={"correo": correo, "password": password})


try:
    # ───────────────────────────────────────────────────────────────
    print("[1] Ninguna ruta de datos responde sin sesión")
    PUBLICAS = {"/health/", "/auth/login", "/auth/refresh", "/auth/logout",
                "/auth/gmail/callback", "/docs", "/redoc", "/openapi.json",
                "/docs/oauth2-redirect"}
    revisadas = abiertas = 0
    # Desde el esquema OpenAPI (en desarrollo existe): lista cada ruta real.
    for path, metodos in aplicacion.openapi()["paths"].items():
        if path in PUBLICAS:
            continue
        url = re.sub(r"\{[^}]+\}", "1", path)
        for metodo in (m.upper() for m in metodos):
            r = cliente.request(metodo, url, json={})
            revisadas += 1
            if r.status_code != 401:
                abiertas += 1
                print(f"        abierta: {metodo} {path} -> {r.status_code}")
    afirmar(abiertas == 0, f"{revisadas} combinaciones ruta/método revisadas, todas piden sesión (401)")

    # ───────────────────────────────────────────────────────────────
    print("\n[2] Login")
    r = login(f"emp{DOMINIO}")
    afirmar(r.status_code == 200, "login correcto -> 200")
    cuerpo = r.json()
    afirmar("refresh_token" not in cuerpo, "el refresh token NO viene en el cuerpo (JavaScript no lo ve)")
    cookie = r.headers.get("set-cookie", "")
    afirmar(NOMBRE_COOKIE_REFRESH in cookie and "HttpOnly" in cookie, "viene en cookie HttpOnly")
    afirmar("samesite=strict" in cookie.lower(), "con SameSite=Strict")
    afirmar("Path=/auth" in cookie, "limitada a la ruta /auth")
    tok_emp = cuerpo["access_token"]
    afirmar(cliente.get("/facturas/estados", headers=bearer(tok_emp)).status_code == 200,
            "con el access token se leen datos")

    r1 = login(f"nadie{DOMINIO}", "x")
    r2 = login(f"emp{DOMINIO}", "incorrecta")
    afirmar(r1.status_code == r2.status_code == 401 and r1.json() == r2.json(),
            "correo inexistente y contraseña mala dan la MISMA respuesta")
    afirmar(login(f"baja{DOMINIO}").status_code == 401, "una cuenta desactivada no entra")

    print("\n[3] Bloqueo por cuenta tras 5 intentos")
    limitador_login_ip.reiniciar("testclient")
    login(f"emp{DOMINIO}")   # un login correcto reinicia el contador de [2]
    for _ in range(4):
        login(f"emp{DOMINIO}", "mala-otra-vez")
    afirmar(login(f"emp{DOMINIO}").status_code == 200, "4 fallos y luego la correcta: entra y se reinicia el contador")
    for _ in range(5):
        login(f"emp{DOMINIO}", "mala-otra-vez")
    afirmar(login(f"emp{DOMINIO}").status_code == 401, "tras 5 fallos, ni la contraseña correcta entra (bloqueada)")
    db.expire_all()
    e = db.query(Usuarios).filter(Usuarios.correo == f"emp{DOMINIO}").first()
    e.bloqueado_hasta = None
    db.commit()

    print("\n[4] Límite por IP")
    limitador_login_ip.reiniciar("testclient")
    codigos = [login(f"x{i}{DOMINIO}", "mala").status_code for i in range(22)]
    afirmar(codigos[-1] == 429, f"después de 20 fallos desde la misma IP -> 429 ({codigos[-3:]})")
    afirmar("retry-after" in {k.lower() for k in login(f"x{DOMINIO}", "m").headers},
            "y dice cuánto esperar (Retry-After)")
    limitador_login_ip.reiniciar("testclient")

    # ───────────────────────────────────────────────────────────────
    print("\n[5] Refresh con cookie")
    c2 = TestClient(aplicacion)
    r = login(f"admin{DOMINIO}", c=c2)
    tok_admin = r.json()["access_token"]
    cookie_vieja = c2.cookies.get(NOMBRE_COOKIE_REFRESH)
    afirmar(c2.post("/auth/refresh").status_code == 403, "sin X-Requested-With -> 403 (anti-CSRF)")
    afirmar(c2.post("/auth/refresh", headers={**XRW, "Origin": "https://malo.example"}).status_code == 403,
            "con Origin de otro sitio -> 403")
    r = c2.post("/auth/refresh", headers=XRW)
    afirmar(r.status_code == 200 and r.json().get("access_token"), "con cookie y encabezado -> token nuevo")
    cookie_nueva = c2.cookies.get(NOMBRE_COOKIE_REFRESH)
    afirmar(cookie_nueva and cookie_nueva != cookie_vieja, "y la cookie ROTA")
    c3 = TestClient(aplicacion)
    c3.cookies.set(NOMBRE_COOKIE_REFRESH, cookie_vieja, path="/auth")
    afirmar(c3.post("/auth/refresh", headers=XRW).status_code == 401, "la cookie vieja ya no sirve")

    db.expire_all()
    fila = db.query(Usuarios).filter(Usuarios.correo == f"admin{DOMINIO}").first()
    afirmar(fila.refresh_token and cookie_nueva not in fila.refresh_token and len(fila.refresh_token) == 64,
            "en la base se guarda el SHA-256, no el token")

    print("\n[6] Logout invalida todo")
    tok_admin = c2.post("/auth/refresh", headers=XRW).json()["access_token"]
    afirmar(cliente.get("/auth/yo", headers=bearer(tok_admin)).status_code == 200, "antes del logout, el token sirve")
    afirmar(c2.post("/auth/logout", headers=XRW).status_code == 204, "logout -> 204")
    afirmar(cliente.get("/auth/yo", headers=bearer(tok_admin)).status_code == 401,
            "el access token emitido antes del logout deja de servir al instante")
    afirmar(c2.post("/auth/refresh", headers=XRW).status_code == 401, "y el refresh también")

    # ───────────────────────────────────────────────────────────────
    print("\n[7] Tokens manipulados")
    db.expire_all()
    ver = db.query(Usuarios).filter(Usuarios.correo == f"emp{DOMINIO}").first().sesion_version
    base = {"sub": f"emp{DOMINIO}", "typ": "access", "iss": "monsort-api",
            "iat": int(time.time()), "exp": int(time.time()) + 600, "ver": ver}
    sin_firma = jwt.encode(base, "", algorithm="HS256").rsplit(".", 1)[0] + "."
    afirmar(cliente.get("/auth/yo", headers=bearer(sin_firma)).status_code == 401, "token sin firma (alg none) -> 401")
    otra_llave = jwt.encode(base, "otra-llave-cualquiera-de-32-caracteres!!", algorithm="HS256")
    afirmar(cliente.get("/auth/yo", headers=bearer(otra_llave)).status_code == 401, "firmado con otra llave -> 401")
    estado = crear_estado_oauth(1)
    afirmar(cliente.get("/auth/yo", headers=bearer(estado)).status_code == 401,
            "el 'state' de OAuth no sirve como sesión")
    vencido = crear_token_acceso({"sub": f"emp{DOMINIO}"}, expires_delta=timedelta(seconds=-5), version=ver)
    afirmar(cliente.get("/auth/yo", headers=bearer(vencido)).status_code == 401, "token vencido -> 401")
    auto_admin = crear_token_acceso({"sub": f"emp{DOMINIO}", "rol": "administrador"}, version=ver)
    afirmar(cliente.post("/auth/gmail/enlace", headers=bearer(auto_admin)).status_code == 403,
            "un token que se dice administrador no da permisos: el rol se lee de la base")

    # ───────────────────────────────────────────────────────────────
    print("\n[8] Registro de usuarios")
    nuevo = {"nombre": "Nuevo", "correo": f"nuevo{DOMINIO}", "password": PASS, "rol": "administrador"}
    afirmar(cliente.post("/auth/registro", json=nuevo).status_code == 401, "sin sesión -> 401 (antes era público)")
    tok_emp = login(f"emp{DOMINIO}").json()["access_token"]
    afirmar(cliente.post("/auth/registro", json=nuevo, headers=bearer(tok_emp)).status_code == 403,
            "un empleado no puede crear cuentas -> 403")
    tok_admin = login(f"admin{DOMINIO}").json()["access_token"]
    debil = {**nuevo, "password": "123456"}
    afirmar(cliente.post("/auth/registro", json=debil, headers=bearer(tok_admin)).status_code == 400,
            "contraseña débil -> 400")
    afirmar(cliente.post("/auth/registro", json={**nuevo, "rol": "superusuario"},
                         headers=bearer(tok_admin)).status_code == 422, "rol inventado -> 422")
    afirmar(cliente.post("/auth/registro", json={**nuevo, "rol": "desarrollador"},
                         headers=bearer(tok_admin)).status_code == 403,
            "un administrador no puede crear un desarrollador")
    afirmar(cliente.post("/auth/registro", json=nuevo, headers=bearer(tok_admin)).status_code == 200,
            "administrador con contraseña fuerte -> 200")

    # ───────────────────────────────────────────────────────────────
    print("\n[9] Gmail OAuth")
    afirmar(cliente.post("/auth/gmail/enlace").status_code == 401, "el enlace de Google pide sesión")
    afirmar(cliente.get("/auth/gmail/iniciar").status_code in (404, 405), "el GET público viejo ya no existe")
    r = cliente.get("/auth/gmail/callback", params={"code": "x", "state": "inventado"})
    afirmar(r.status_code == 400, "callback con 'state' falso -> 400 y no toca el token")
    r = cliente.get("/auth/gmail/callback", params={"code": "x", "state": "<script>alert(1)</script>"})
    afirmar("<script>" not in r.text, "el callback no refleja lo que recibe (XSS)")

    # ───────────────────────────────────────────────────────────────
    print("\n[10] Encabezados y descargas")
    r = cliente.get("/health/")
    for h in ("x-content-type-options", "x-frame-options", "content-security-policy", "referrer-policy"):
        afirmar(h in r.headers, f"respuesta trae {h}")
    oc = OrdenesCompra(numero_oc="PRUEBA-1", archivo=b"%PDF-1.4 prueba",
                       nombre_archivo='malo"\r\nSet-Cookie: x=1;.pdf', message_id="prueba-seguridad",
                       hash_archivo="f" * 64)
    db.add(oc)
    db.commit()
    r = cliente.get(f"/ordenes-compra/{oc.id}/archivo", headers=bearer(tok_admin))
    cd = r.headers.get("content-disposition", "")
    afirmar(r.status_code == 200 and "\r" not in cd and "\n" not in cd and 'malo"' not in cd,
            f"nombre de archivo saneado en Content-Disposition ({cd[:60]}…)")
    afirmar("set-cookie" not in {k.lower() for k in r.headers}, "no se puede inyectar un encabezado")
    afirmar(r.headers.get("cache-control", "").startswith("private, no-store"), "descargas sin caché")

    print("\n[11] Errores sin detalles internos")
    r = cliente.get("/reportes/detalle/999999999", headers=bearer(tok_admin))
    afirmar("Traceback" not in r.text and "sqlalchemy" not in r.text.lower(), "sin traza ni SQL en la respuesta")

finally:
    limitador_login_ip.reiniciar("testclient")
    limitador_refresh_ip.reiniciar("testclient")
    limpiar()
    db.close()

print()
if fallos:
    print(f"{len(fallos)} FALLA(S):")
    for f in fallos:
        print("  -", f)
    sys.exit(1)
print("Todas las pruebas de seguridad pasaron.")
