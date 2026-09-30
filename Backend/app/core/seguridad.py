"""
Contraseñas, tokens y límites de intentos.

Modelo de sesión (desde el 30/09/2026)
--------------------------------------
- ACCESS TOKEN (JWT, 15 min): viaja en `Authorization: Bearer`. El frontend
  lo guarda SOLO en memoria, nunca en localStorage: un script inyectado
  (XSS) puede leer localStorage, no una variable de un módulo cerrado.
- REFRESH TOKEN (aleatorio, 7 días): viaja en una cookie HttpOnly + Secure +
  SameSite=Strict, limitada a la ruta /auth. JavaScript no puede leerla; el
  navegador la manda solo a /auth/refresh y /auth/logout, y solo desde el
  mismo sitio. En la base se guarda su SHA-256, no el token.
- Cada refresh ROTA el refresh token. Un token viejo ya no sirve.
- El JWT lleva "ver" = Usuarios.sesion_version. Cerrar sesión o cambiar la
  contraseña sube la versión y mata al instante los access token vivos.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import secrets
import threading
import time
import uuid
from collections import deque
from datetime import datetime, timedelta, timezone

import bcrypt
from jose import JWTError, jwt

from app.core.config import settings

logger = logging.getLogger("app.seguridad")

# Solo HMAC. Un .env con ALGORITMO=none o RS256 sin llaves no debe poder
# abrir la puerta: se ignora y se usa HS256.
ALGORITMOS_PERMITIDOS = {"HS256", "HS384", "HS512"}
ALGORITMO = settings.ALGORITMO if settings.ALGORITMO in ALGORITMOS_PERMITIDOS else "HS256"

EMISOR = "monsort-api"
REFRESH_DIAS = 7
# Tope duro aunque el .env pida más: un access token robado sirve poco.
ACCESS_MAX_MIN = 60


# ─────────────────────────────────────────────
# Contraseñas
# ─────────────────────────────────────────────

# bcrypt ignora (o rechaza, en bcrypt>=5) lo que pase de 72 bytes.
LARGO_MIN_PASSWORD = 12
LARGO_MAX_PASSWORD_BYTES = 72

_COMUNES = {
    "123456789012", "contraseña123", "password1234", "monsort12345",
    "qwertyuiop12", "administrador", "abcdefghijkl", "000000000000",
}

# Hash de una contraseña que nadie tiene: se compara contra él cuando el
# correo no existe, para que la respuesta tarde lo mismo y no delate qué
# correos están registrados.
_HASH_SEÑUELO = bcrypt.hashpw(secrets.token_bytes(16), bcrypt.gensalt()).decode()


def validar_password(password: str, correo: str | None = None) -> str | None:
    """None si la contraseña sirve; si no, el motivo en español."""
    if len(password) < LARGO_MIN_PASSWORD:
        return f"La contraseña debe tener al menos {LARGO_MIN_PASSWORD} caracteres."
    if len(password.encode("utf-8")) > LARGO_MAX_PASSWORD_BYTES:
        return "La contraseña es demasiado larga (máximo 72 bytes)."
    if password.lower() in _COMUNES or len(set(password)) < 5:
        return "La contraseña es demasiado fácil de adivinar."
    if correo and correo.split("@")[0].lower() in password.lower():
        return "La contraseña no debe contener el correo."
    clases = sum([
        any(c.islower() for c in password), any(c.isupper() for c in password),
        any(c.isdigit() for c in password), any(not c.isalnum() for c in password),
    ])
    if clases < 3:
        return "Usa al menos tres de: minúsculas, mayúsculas, números y símbolos."
    return None


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt(rounds=12)).decode("utf-8")


def verify_password(plain_password: str, hashed_password: str | None) -> bool:
    """False ante cualquier problema (hash corrupto, contraseña > 72 bytes)."""
    try:
        return bcrypt.checkpw(
            plain_password.encode("utf-8")[:LARGO_MAX_PASSWORD_BYTES],
            (hashed_password or _HASH_SEÑUELO).encode("utf-8"),
        )
    except (ValueError, TypeError):
        # "Sistema Automatico" tiene un texto que no es hash bcrypt; antes
        # esto tronaba con 500 en el login.
        return False


def comparar_con_señuelo(plain_password: str) -> None:
    verify_password(plain_password, _HASH_SEÑUELO)


# ─────────────────────────────────────────────
# Access token (JWT)
# ─────────────────────────────────────────────

def _minutos_access() -> int:
    return max(1, min(int(settings.TOKEN_ACCESO_MIN_EXPIRACION or 15), ACCESS_MAX_MIN))


def crear_token_acceso(data: dict, expires_delta: timedelta | None = None,
                       version: int = 0) -> str:
    ahora = datetime.now(timezone.utc)
    expira = ahora + (expires_delta or timedelta(minutes=_minutos_access()))
    claims = {
        **data,
        "typ": "access",
        "iss": EMISOR,
        "iat": int(ahora.timestamp()),
        "exp": int(expira.timestamp()),
        "jti": uuid.uuid4().hex,
        "ver": int(version or 0),
    }
    return jwt.encode(claims, settings.SECRET_KEY, algorithm=ALGORITMO)


def decode_token(token: str, tipo: str = "access") -> dict | None:
    """Claims si el token es válido, vigente, de este emisor y del tipo pedido."""
    try:
        payload = jwt.decode(
            token, settings.SECRET_KEY, algorithms=[ALGORITMO],
            issuer=EMISOR, options={"require_exp": True, "require_iat": True},
        )
    except JWTError:
        return None
    if payload.get("typ") != tipo:
        # Un token de "estado OAuth" no sirve como sesión, ni al revés.
        return None
    return payload


# ─────────────────────────────────────────────
# Refresh token (opaco, en cookie)
# ─────────────────────────────────────────────

NOMBRE_COOKIE_REFRESH = "monsort_refresh"


def crear_refresh_token() -> tuple[str, datetime]:
    token = secrets.token_urlsafe(48)
    # Naive en hora del servidor: así compara con la columna DateTime.
    expiracion = datetime.now() + timedelta(days=REFRESH_DIAS)
    return token, expiracion


def huella_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def huellas_iguales(a: str | None, b: str | None) -> bool:
    return bool(a and b) and hmac.compare_digest(a, b)


# ─────────────────────────────────────────────
# Estado OAuth de Gmail (anti-CSRF del callback)
# ─────────────────────────────────────────────

def crear_estado_oauth(id_usuario: int, horas: int = 24) -> str:
    """
    'state' firmado para el flujo OAuth de Gmail. El callback solo acepta
    un 'state' emitido por este servidor, para un administrador, y vigente:
    sin esto cualquiera podía completar el flujo con SU cuenta de Gmail y
    el sistema empezaba a leer el buzón equivocado.
    """
    return jwt.encode(
        {
            "sub": str(id_usuario), "typ": "oauth_gmail", "iss": EMISOR,
            "iat": int(time.time()), "exp": int(time.time()) + horas * 3600,
            "jti": uuid.uuid4().hex,
        },
        settings.SECRET_KEY, algorithm=ALGORITMO,
    )


# ─────────────────────────────────────────────
# Límite de intentos por IP (memoria del proceso)
#
# Funciona porque la API corre con --workers 1 (obligatorio por APScheduler).
# nginx aplica además su propio limit_req a /api/auth/.
# ─────────────────────────────────────────────

class LimitadorIntentos:
    def __init__(self, maximo: int, ventana_seg: int):
        self.maximo = maximo
        self.ventana = ventana_seg
        self._eventos: dict[str, deque] = {}
        self._candado = threading.Lock()

    def _limpiar(self, cola: deque, ahora: float) -> None:
        while cola and ahora - cola[0] > self.ventana:
            cola.popleft()

    def bloqueado(self, clave: str) -> int:
        """Segundos que faltan si la clave está bloqueada; 0 si no."""
        ahora = time.monotonic()
        with self._candado:
            cola = self._eventos.get(clave)
            if not cola:
                return 0
            self._limpiar(cola, ahora)
            if len(cola) >= self.maximo:
                return int(self.ventana - (ahora - cola[0])) + 1
            return 0

    def registrar(self, clave: str) -> None:
        ahora = time.monotonic()
        with self._candado:
            cola = self._eventos.setdefault(clave, deque())
            self._limpiar(cola, ahora)
            cola.append(ahora)
            # Que el diccionario no crezca sin fin con IPs de paso
            if len(self._eventos) > 10_000:
                for k in list(self._eventos)[:5_000]:
                    self._eventos.pop(k, None)

    def reiniciar(self, clave: str) -> None:
        with self._candado:
            self._eventos.pop(clave, None)


# 20 intentos fallidos por IP cada 15 minutos
limitador_login_ip = LimitadorIntentos(maximo=20, ventana_seg=15 * 60)
# 60 refresh por IP cada 5 minutos (la app hace uno cada ~15 min por pestaña)
limitador_refresh_ip = LimitadorIntentos(maximo=60, ventana_seg=5 * 60)

# Bloqueo por cuenta (persistente, en Usuarios)
MAX_FALLOS_CUENTA = 5
MINUTOS_BLOQUEO_CUENTA = 15
