"""Route guardians (docs/designs/route-guardian-contracts.md, G1/G3/G4/G5): the `guardian`-role
user who watches a route and whose phone the truck's ESP32 texts on every critical alert."""
from __future__ import annotations

from typing import Iterable, Optional

from beanie.operators import In
from fastapi import HTTPException, status

from app.ids import get_by_id, to_object_id
from app.models.common import Role, RouteStatus
from app.models.route import Route
from app.models.user import User
from app.phone import GUARDIAN_PHONE_ERROR, is_e164, normalize_phone

# A guardian assigned to a route in one of these can't be deactivated, demoted or deleted (G3).
# `in_progress_alert` is an in-progress route too (`ACTIVE_ROUTE_STATUSES`), so it counts.
GUARDED_ROUTE_STATUSES = (RouteStatus.SCHEDULED, RouteStatus.IN_PROGRESS, RouteStatus.IN_PROGRESS_ALERT)


def guardian_problem(user: Optional[User]) -> Optional[str]:
    """Why `user` can't guard a route, as the G1 422 detail, or None if it can. The order is
    the contract's: missing, then wrong role or inactive, then phone."""
    if user is None:
        return "id_guardian not found"
    if user.role != Role.GUARDIAN or not user.is_active:
        return "id_guardian must be an active guardian"
    if not is_e164(user.phone_number):
        return "guardian has no valid E.164 phone"
    return None


def is_usable_guardian(user: Optional[User]) -> bool:
    """Exists, active, `guardian` role and an E.164 phone (G5)."""
    return guardian_problem(user) is None


def guardian_phone(phone_number: object) -> str:
    """A guardian's phone, normalised; 422 unless it's E.164 (route-guardian contracts, G2): it's
    the number the truck's ESP32 texts on every critical alert."""
    if not isinstance(phone_number, str) or not is_e164(phone_number):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, GUARDIAN_PHONE_ERROR)
    return normalize_phone(phone_number)


async def check_guardian(id_guardian: str) -> User:
    """The guardian for a route create/update, or 422 with the G1 detail."""
    user = await get_by_id(User, id_guardian)
    problem = guardian_problem(user)
    if problem is not None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, problem)
    return user


async def load_guardians(routes: Iterable[Route]) -> dict[str, User]:
    """Every guardian the routes point at, by user id, in one query (so `/api/live` stays one
    users query however many routes it returns). A deleted guardian is simply absent."""
    ids = {r.id_guardian for r in routes if r.id_guardian}
    oids = [oid for oid in (to_object_id(i) for i in ids) if oid is not None]
    if not oids:
        return {}
    users = await User.find(In(User.id, oids)).to_list()
    return {str(u.id): u for u in users}


async def guardian_for(route: Route) -> Optional[User]:
    return await get_by_id(User, route.id_guardian) if route.id_guardian else None


async def guarded_route_count(user_id: str) -> int:
    """How many scheduled/in-progress routes this user guards (G3)."""
    return await Route.find(
        Route.id_guardian == user_id,
        {"operative_status": {"$in": [s.value for s in GUARDED_ROUTE_STATUSES]}},
    ).count()


async def ensure_not_guarding(user_id: str) -> None:
    """409 while the user guards an active route (G3)."""
    count = await guarded_route_count(user_id)
    if count:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"guardian is assigned to {count} active route(s); reassign them first",
        )
