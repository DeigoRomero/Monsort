from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator


class LoginRequest(BaseModel):
    correo: EmailStr
    # Tope de largo: sin él, alguien manda 10 MB de "contraseña" y bcrypt
    # (o el parser) se lo come en cada intento.
    password: str = Field(min_length=1, max_length=256)


class UsuarioResponse(BaseModel):
    id_usuario: int
    correo: EmailStr
    nombre: str
    rol: str
    model_config = ConfigDict(from_attributes=True)


class LoginResponse(BaseModel):
    """
    El refresh token ya NO viene aquí: va en una cookie HttpOnly que
    JavaScript no puede leer.
    """
    access_token: str
    token_type: str = "bearer"
    expira_en: int            # segundos
    usuario: UsuarioResponse


class RegistroRequest(BaseModel):
    nombre: str = Field(min_length=2, max_length=120)
    correo: EmailStr
    password: str = Field(min_length=1, max_length=256)
    rol: str

    @field_validator("rol")
    @classmethod
    def _rol(cls, v: str) -> str:
        v = (v or "").strip().lower()
        if v not in ("empleado", "administrador", "desarrollador"):
            raise ValueError("Rol no válido")
        return v


class CambiarPasswordRequest(BaseModel):
    actual: str = Field(min_length=1, max_length=256)
    nueva: str = Field(min_length=1, max_length=256)
