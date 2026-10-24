from __future__ import annotations

from datetime import datetime, timezone

from beanie import PydanticObjectId
from fastapi import APIRouter, Depends, HTTPException, status

from app.auth.dependencies import get_current_user, invalidate_device_cache, require_role
from app.auth.security import generate_device_api_key, hash_secret, key_fingerprint
from app.models.common import Role
from app.models.truck import Truck
from app.schemas.auth import DeviceKeyResponse
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


@router.post(
    "/{truck_id}/rotate-key",
    response_model=DeviceKeyResponse,
    dependencies=[Depends(require_role(Role.ROOT_ADMIN, Role.ADMIN))],
)
async def rotate_device_key(truck_id: str) -> DeviceKeyResponse:
    """Issues a new device API key for this truck, invalidating any previous one. The plaintext
    key is returned exactly once here and never again. The dashboard turns it into an
    `argus-device.env` for the truck's Raspberry Pi (`cv-argus`), which hands it to the ESP32
    over Bluetooth (`CONFIG`). `admin` may do this too, since it owns the truck roster."""
    truck = await _get_or_404(truck_id)
    plaintext = generate_device_api_key()
    truck.device_api_key_hash = hash_secret(plaintext)
    truck.device_api_key_fingerprint = key_fingerprint(plaintext)
    truck.device_key_issued_at = datetime.now(timezone.utc)
    truck.device_last_seen_at = None
    await truck.touch_and_save()
    invalidate_device_cache(str(truck.id))
    return DeviceKeyResponse(id_truck=str(truck.id), device_api_key=plaintext)


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
