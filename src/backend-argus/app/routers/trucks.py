from __future__ import annotations

from beanie import PydanticObjectId
from fastapi import APIRouter, Depends, HTTPException, status

from app.auth.dependencies import get_current_user, require_role
from app.auth.security import generate_device_api_key, hash_secret
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
    return Message(detail="Truck deleted")


@router.post(
    "/{truck_id}/rotate-key",
    response_model=DeviceKeyResponse,
    dependencies=[Depends(require_role(Role.ROOT_ADMIN))],
)
async def rotate_device_key(truck_id: str) -> DeviceKeyResponse:
    """Issues a new device API key for this truck's ESP32, invalidating any previous one. The
    plaintext key is returned exactly once here and never again — provision it into the ESP32's
    firmware config out of band."""
    truck = await _get_or_404(truck_id)
    plaintext = generate_device_api_key()
    truck.device_api_key_hash = hash_secret(plaintext)
    await truck.touch_and_save()
    return DeviceKeyResponse(id_truck=str(truck.id), device_api_key=plaintext)
