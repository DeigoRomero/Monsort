from pydantic import BaseModel, ConfigDict, EmailStr

class LoginRequest(BaseModel):
    correo: EmailStr
    password: str

class UsuarioResponse(BaseModel):
    id_usuario: int
    correo: EmailStr
    nombre: str
    rol: str
    model_config = ConfigDict(from_attributes=True)

class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    refresh_token: str
    usuario: UsuarioResponse

class RefreshTokenRequest(BaseModel):
    refresh_token: str

class RefreshTokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    