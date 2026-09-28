from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    DATABASE_URL: str
    SECRET_KEY: str 
    ALGORITMO: str 
    TOKEN_ACCESO_MIN_EXPIRACION: int 
    GMAIL_CLIENT_ID: str
    GMAIL_CLIENT_SECRET: str
    GMAIL_REFRESH_TOKEN: str
    banxico_token: str | None = None
    SAT_CER_PATH: str | None = None
    SAT_KEY_PATH: str | None = None
    SAT_KEY_PASSWORD: str | None = None
    GMAIL_REDIRECT_URI: str = "http://localhost:8000/auth/gmail/callback"
    CORS_ORIGINS: str = "http://localhost:5173,http://127.0.0.1:5173"
    ENTORNO: str = "desarrollo"  # desarrollo, produccion
    RFC_EMPRESA: str = "MSF140227BF7"
    # Portal de CFDI del SAT (Backend/sat_portal). Usa las mismas SAT_CER_PATH,
    # SAT_KEY_PATH y SAT_KEY_PASSWORD de la e.firma.
    SAT_PORTAL_ACTIVO: bool = False            # prende los jobs programados
    SAT_PORTAL_PHP: str = "php"                # ruta al binario de PHP
    SAT_PORTAL_SCRIPT: str | None = None       # default: Backend/sat_portal/consultar_portal.php
    # El SAT (cfdiau.sat.gob.mx) usa llaves DH de 1024 bits y el OpenSSL de
    # Ubuntu 26.04 las rechaza ("dh key too small", confirmado 2026-09-28).
    # Encendido por default; solo afecta las conexiones del script del portal.
    SAT_PORTAL_SECLEVEL1: bool = True
    SAT_PORTAL_TIMEOUT_SEGUNDOS: int = 900
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

settings = Settings()