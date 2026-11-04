from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, EmailStr

from app.models.common import Role
from app.schemas.common import Sha256HexDigest


class LoginRequest(BaseModel):
    email: EmailStr
    password: Sha256HexDigest


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


class MeUpdate(BaseModel):
    """`PUT /api/auth/me` — the self-service profile edit every authenticated role gets,
    regardless of role. Deliberately has **no** `role`/`is_active` fields at all: not just
    permission-checked but structurally impossible to send, so a user can never smuggle a
    privilege escalation into their own profile edit. Changing role/active-status stays
    exclusively `root_admin`'s job via `/api/users/{id}` (and, scoped to guardians, `admin`'s
    via the same endpoint)."""

    email: Optional[EmailStr] = None
    password: Optional[Sha256HexDigest] = None
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    phone_number: Optional[str] = None


class DeviceKeyResponse(BaseModel):
    """Returned exactly once, from `POST /api/trucks/{id}/rotate-key` — the plaintext key is
    never stored or shown again after this response."""

    id_truck: str
    device_api_key: str
    # The phone `argus-device.env` carries as ARGUS_ALERT_PHONE, and where it came from
    # (route-guardian contracts, G5): a route's guardian, else the truck's fallback phone.
    alert_phone: Optional[str] = None
    alert_phone_source: Optional[Literal["route_guardian", "truck"]] = None
    id_route: Optional[str] = None
    guardian_name: Optional[str] = None


class RotateKeyRequest(BaseModel):
    """Optional body of `POST /api/trucks/{id}/rotate-key`: the route whose guardian's phone
    the env file should carry (G5). No body, or `{}`, resolves it from the truck's routes."""

    id_route: Optional[str] = None
