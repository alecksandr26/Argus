"""FastAPI dependencies for user-JWT auth, role-based access control, and the separate
device-API-key path ESP32 writes use. See the backend CLAUDE.md's "Auth design" section for
the full rationale.
"""
from __future__ import annotations

from typing import Optional

from beanie import PydanticObjectId
from fastapi import Depends, Header, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from starlette.concurrency import run_in_threadpool

from app.auth.security import decode_access_token, key_fingerprint, verify_secret
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


async def device_key_matches(device_key: Optional[str], truck: Optional[Truck]) -> bool:
    """bcrypt-verifies `device_key` against `truck`'s stored hash, off the event loop (bcrypt is
    deliberately slow and would otherwise stall every other request, BE-19)."""
    if not device_key or truck is None or not truck.device_api_key_hash:
        return False
    return await run_in_threadpool(verify_secret, device_key, truck.device_api_key_hash)


async def authorize_device_or_user(
    *,
    truck: Optional[Truck],
    device_key: Optional[str],
    user: Optional[User],
    allowed_roles: tuple[Role, ...],
) -> bool:
    """The "either a device key for this truck, or a sufficiently-privileged user JWT" check
    used by the alert- and status-ingestion endpoints. Returns True when the caller is the
    truck's device (some rules apply only to devices), False for an allowed user.

    401 if neither a valid key nor a valid token was presented; 403 if a valid token belongs to a
    role that isn't allowed (BE-16) -- authenticated, just not permitted.

    A device key scopes a write to *its own* truck's data for free: the key is checked against
    the specific `truck` the write targets (resolved by the caller from the route being written
    to), not against a global secret, so a compromised key can't be replayed against a different
    truck's routes.
    """
    if await device_key_matches(device_key, truck):
        return True
    if user is not None:
        if user.role in allowed_roles:
            return False
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Not permitted for this role")
    raise HTTPException(
        status.HTTP_401_UNAUTHORIZED,
        "Requires a valid device API key for this truck, or an authorized user token",
    )


async def get_device_truck(
    device_key: Optional[str] = Depends(get_device_api_key),
) -> Truck:
    """The truck a device key belongs to, for endpoints where the device identifies itself by
    key alone (no route id in the request). 401 for a missing or unknown key."""
    if device_key:
        truck = await Truck.find_one(Truck.device_api_key_fingerprint == key_fingerprint(device_key))
        if truck is not None and await device_key_matches(device_key, truck):
            return truck
        # Keys issued before fingerprints existed: fall back to checking each such truck.
        legacy = await Truck.find(
            Truck.device_api_key_fingerprint == None,  # noqa: E711 (Beanie query syntax)
            Truck.device_api_key_hash != None,  # noqa: E711
        ).to_list()
        for truck in legacy:
            if await device_key_matches(device_key, truck):
                return truck
    raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Requires a valid device API key")
