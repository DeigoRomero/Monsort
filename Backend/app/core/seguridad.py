import secrets

from app.core.config import settings
import bcrypt
from datetime import datetime, timedelta, timezone
from jose import jwt, JWTError

def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")

def verify_password(plain_password: str, hashed_password: str) -> bool:
    return bcrypt.checkpw(plain_password.encode("utf-8"), hashed_password.encode("utf-8"))

def crear_token_acceso(data: dict, expires_delta: timedelta | None = None) -> str:
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.now(timezone.utc) + expires_delta
    else:
        expire = datetime.now(timezone.utc) + timedelta(minutes=settings.TOKEN_ACCESO_MIN_EXPIRACION)
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, settings.SECRET_KEY, algorithm=settings.ALGORITMO)
    return encoded_jwt

def decode_token(token: str) -> dict:
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITMO])
        return payload
    except JWTError:
        return None
    
def crear_refresh_token() -> tuple[str, datetime]:
    token = secrets.token_hex(32)
    expiracion = datetime.now(timezone.utc) + timedelta(days=7)
    return token, expiracion
