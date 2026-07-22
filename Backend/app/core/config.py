from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    DATABASE_URL: str
    SECRET_KEY: str 
    ALGORITMO: str 
    TOKEN_ACCESO_MIN_EXPIRACION: int 
    GMAIL_CLIENT_ID: str
    GMAIL_CLIENT_SECRET: str
    GMAIL_REFRESH_TOKEN: str
    model_config = SettingsConfigDict(env_file=".env")

settings = Settings()