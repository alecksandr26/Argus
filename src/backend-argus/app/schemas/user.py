from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, EmailStr

from app.models.common import Role
from app.schemas.common import Sha256HexDigest


class UserCreate(BaseModel):
    email: EmailStr
    password: Sha256HexDigest
    role: Role
    first_name: str
    last_name: str
    phone_number: str
    is_active: bool = True


class UserUpdate(BaseModel):
    email: Optional[EmailStr] = None
    password: Optional[Sha256HexDigest] = None
    role: Optional[Role] = None
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    phone_number: Optional[str] = None
    is_active: Optional[bool] = None


class UserOut(BaseModel):
    id_user: str
    email: EmailStr
    role: Role
    first_name: str
    last_name: str
    phone_number: str
    is_active: bool
    last_login: Optional[datetime]
    created_at: datetime
    updated_at: datetime
