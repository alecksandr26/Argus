"""FastAPI dependencies for user-JWT auth, role-based access control, and the separate
device-API-key path ESP32 writes use. See the backend CLAUDE.md's "Auth design" section for
the full rationale.
"""
from __future__ import annotations

from typing import Optional

from beanie import PydanticObjectId
from fastapi import Depends, Header, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.auth.security import decode_access_token, verify_secret
from app.models.common import Role
from app.models.truck import Truck
from app.models.user import User

_bearer_scheme = HTTPBearer(auto_error=False)


async def get_current_user_optional(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(_bearer_scheme),
) -> Optional[User]:
    if credentials is None:
        return None
    claims = decode_access_token(credentials.credentials)
    if claims is None:
        return None
    try:
        user = await User.get(PydanticObjectId(claims["sub"]))
    except Exception:
        return None
    if user is None or not user.is_active:
        return None
    return user


async def get_current_user(
    user: Optional[User] = Depends(get_current_user_optional),
) -> User:
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not authenticated")
    return user


def require_role(*roles: Role):
    """`Depends(require_role(Role.ROOT_ADMIN))` — 403s if the current user's role isn't in
    `roles`. Chains on top of `get_current_user`, so it also 401s if there's no valid token."""

    async def _check(user: User = Depends(get_current_user)) -> User:
        if user.role not in roles:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Not permitted for this role")
        return user

    return _check


async def get_device_api_key(
    x_device_api_key: Optional[str] = Header(default=None, alias="X-Device-Api-Key"),
) -> Optional[str]:
    return x_device_api_key


async def authorize_device_or_user(
    *,
    truck: Optional[Truck],
    device_key: Optional[str],
    user: Optional[User],
    allowed_roles: tuple[Role, ...],
) -> None:
    """The "either a device key for this truck, or a sufficiently-privileged user JWT" check
    used by the alert- and status-ingestion endpoints. Raises 401 if neither validates.

    A device key scopes a write to *its own* truck's data for free: the key is checked against
    the specific `truck` the write targets (resolved by the caller from the route being written
    to), not against a global secret, so a compromised key can't be replayed against a different
    truck's routes.
    """
    if device_key and truck is not None and truck.device_api_key_hash:
        if verify_secret(device_key, truck.device_api_key_hash):
            return
    if user is not None and user.role in allowed_roles:
        return
    raise HTTPException(
        status.HTTP_401_UNAUTHORIZED,
        "Requires a valid device API key for this truck, or an authorized user token",
    )
