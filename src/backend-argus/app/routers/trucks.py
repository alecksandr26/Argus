from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from beanie import PydanticObjectId
from fastapi import APIRouter, Body, Depends, HTTPException, status

from app.auth.dependencies import get_current_user, invalidate_device_cache, require_role
from app.auth.security import generate_device_api_key, hash_secret, key_fingerprint
from app.guardians import guardian_for, is_usable_guardian
from app.ids import get_by_id
from app.models.common import Role, RouteStatus
from app.models.route import Route
from app.models.truck import Truck
from app.phone import normalize_phone
from app.report import active_route_for
from app.schemas.auth import DeviceKeyResponse, RotateKeyRequest
from app.schemas.common import Message
from app.schemas.truck import TruckCreate, TruckOut, TruckUpdate
from app.serializers import truck_out

router = APIRouter(prefix="/api/trucks", tags=["trucks"])


async def _get_or_404(truck_id: str) -> Truck:
    try:
        oid = PydanticObjectId(truck_id)
    except Exception:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Truck not found")
    truck = await Truck.get(oid)
    if truck is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Truck not found")
    return truck


@router.get("", response_model=list[TruckOut], dependencies=[Depends(get_current_user)])
async def list_trucks() -> list[TruckOut]:
    # NOTE: the ER model has no User<->Truck link, so a `truck_driver` role currently sees the
    # full fleet like everyone else — real "own truck only" scoping needs a schema addition not
    # in this pass's scope. See the backend CLAUDE.md's "Known RBAC gap" section.
    return [truck_out(t) for t in await Truck.find_all().to_list()]


@router.post(
    "",
    response_model=TruckOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_role(Role.ROOT_ADMIN, Role.ADMIN))],
)
async def create_truck(body: TruckCreate) -> TruckOut:
    truck = Truck(**body.model_dump())
    await truck.insert()
    return truck_out(truck)


@router.get("/{truck_id}", response_model=TruckOut, dependencies=[Depends(get_current_user)])
async def get_truck(truck_id: str) -> TruckOut:
    return truck_out(await _get_or_404(truck_id))


@router.put(
    "/{truck_id}",
    response_model=TruckOut,
    dependencies=[Depends(require_role(Role.ROOT_ADMIN, Role.ADMIN))],
)
async def update_truck(truck_id: str, body: TruckUpdate) -> TruckOut:
    truck = await _get_or_404(truck_id)
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(truck, field, value)
    await truck.touch_and_save()
    return truck_out(truck)


@router.delete(
    "/{truck_id}",
    response_model=Message,
    dependencies=[Depends(require_role(Role.ROOT_ADMIN, Role.ADMIN))],
)
async def delete_truck(truck_id: str) -> Message:
    truck = await _get_or_404(truck_id)
    await truck.delete()
    invalidate_device_cache(str(truck.id))
    return Message(detail="Truck deleted")


async def _resolve_alert_phone(truck: Truck, given: Optional[Route]) -> dict:
    """The env file's SMS phone (route-guardian contracts, G5), first match wins: the given
    route's guardian, the in-progress route's, the earliest scheduled route's (each only if that
    guardian is usable), then `Truck.alert_phone`. `id_route` names the route considered (the
    first of those that exists), `guardian_name` the guardian whose phone was picked."""
    truck_id = str(truck.id)
    scheduled = (
        await Route.find(Route.id_truck == truck_id, Route.operative_status == RouteStatus.SCHEDULED)
        .sort(+Route.estimated_departure)
        .first_or_none()
    )
    candidates = [r for r in (given, await active_route_for(truck_id), scheduled) if r is not None]
    for route in candidates:
        guardian = await guardian_for(route)
        if is_usable_guardian(guardian):
            return {
                "alert_phone": normalize_phone(guardian.phone_number),
                "alert_phone_source": "route_guardian",
                "id_route": str(route.id),
                "guardian_name": f"{guardian.first_name} {guardian.last_name}",
            }
    if truck.alert_phone:
        return {
            "alert_phone": truck.alert_phone,
            "alert_phone_source": "truck",
            "id_route": str(candidates[0].id) if candidates else None,
            "guardian_name": None,
        }
    return {"alert_phone": None, "alert_phone_source": None, "id_route": None, "guardian_name": None}


@router.post(
    "/{truck_id}/rotate-key",
    response_model=DeviceKeyResponse,
    dependencies=[Depends(require_role(Role.ROOT_ADMIN, Role.ADMIN))],
)
async def rotate_device_key(
    truck_id: str, body: Optional[RotateKeyRequest] = Body(default=None)
) -> DeviceKeyResponse:
    """Issues a new device API key for this truck, invalidating any previous one. The plaintext
    key is returned exactly once here and never again. The dashboard turns it into an
    `argus-device.env` for the truck's Raspberry Pi (`cv-argus`), which hands it to the ESP32
    over Bluetooth (`CONFIG`). `admin` may do this too, since it owns the truck roster.

    The optional body `{"id_route": ...}` picks the route whose guardian's phone goes into that
    file (`alert_phone`, G5); an unknown route or another truck's is 422, checked before the key
    is rotated so a bad request never invalidates the old key."""
    truck = await _get_or_404(truck_id)
    given: Optional[Route] = None
    if body is not None and body.id_route is not None:
        given = await get_by_id(Route, body.id_route)
        if given is None or given.id_truck != str(truck.id):
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY, "id_route not found for this truck"
            )
    phone = await _resolve_alert_phone(truck, given)
    plaintext = generate_device_api_key()
    truck.device_api_key_hash = hash_secret(plaintext)
    truck.device_api_key_fingerprint = key_fingerprint(plaintext)
    truck.device_key_issued_at = datetime.now(timezone.utc)
    truck.device_last_seen_at = None
    await truck.touch_and_save()
    invalidate_device_cache(str(truck.id))
    return DeviceKeyResponse(id_truck=str(truck.id), device_api_key=plaintext, **phone)


@router.delete(
    "/{truck_id}/device-key",
    response_model=TruckOut,
    dependencies=[Depends(require_role(Role.ROOT_ADMIN, Role.ADMIN))],
)
async def revoke_device_key(truck_id: str) -> TruckOut:
    """Revokes this truck's device key outright (a lost or compromised Pi). Every later device
    request with it is a 401 until a new key is issued via rotate-key."""
    truck = await _get_or_404(truck_id)
    truck.device_api_key_hash = None
    truck.device_api_key_fingerprint = None
    truck.device_key_issued_at = None
    truck.device_last_seen_at = None
    await truck.touch_and_save()
    invalidate_device_cache(str(truck.id))
    return truck_out(truck)
