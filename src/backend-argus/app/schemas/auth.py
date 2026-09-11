from __future__ import annotations

from pydantic import BaseModel, EmailStr

from app.models.common import Role


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class LoginUser(BaseModel):
    id_user: str
    email: EmailStr
    role: Role
    first_name: str
    last_name: str


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: LoginUser


class DeviceKeyResponse(BaseModel):
    """Returned exactly once, from `POST /api/trucks/{id}/rotate-key` — the plaintext key is
    never stored or shown again after this response."""

    id_truck: str
    device_api_key: str
