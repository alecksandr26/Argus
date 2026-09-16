from __future__ import annotations

from beanie import PydanticObjectId
from fastapi import APIRouter, Depends, HTTPException, status

from app.auth.dependencies import get_current_user, require_role
from app.models.common import Role
from app.models.driver import Driver
from app.schemas.common import Message
from app.schemas.driver import DriverCreate, DriverOut, DriverUpdate
from app.serializers import driver_out

router = APIRouter(prefix="/api/drivers", tags=["drivers"])


async def _get_or_404(driver_id: str) -> Driver:
    try:
        oid = PydanticObjectId(driver_id)
    except Exception:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Driver not found")
    driver = await Driver.get(oid)
    if driver is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Driver not found")
    return driver


@router.get("", response_model=list[DriverOut], dependencies=[Depends(get_current_user)])
async def list_drivers() -> list[DriverOut]:
    # Same "no User<->Driver link in the ER model" caveat as trucks.py's list_trucks.
    return [driver_out(d) for d in await Driver.find_all().to_list()]


@router.post(
    "",
    response_model=DriverOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_role(Role.ROOT_ADMIN, Role.ADMIN))],
)
async def create_driver(body: DriverCreate) -> DriverOut:
    driver = Driver(**body.model_dump())
    await driver.insert()
    return driver_out(driver)


@router.get("/{driver_id}", response_model=DriverOut, dependencies=[Depends(get_current_user)])
async def get_driver(driver_id: str) -> DriverOut:
    return driver_out(await _get_or_404(driver_id))


@router.put(
    "/{driver_id}",
    response_model=DriverOut,
    dependencies=[Depends(require_role(Role.ROOT_ADMIN, Role.ADMIN))],
)
async def update_driver(driver_id: str, body: DriverUpdate) -> DriverOut:
    driver = await _get_or_404(driver_id)
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(driver, field, value)
    await driver.touch_and_save()
    return driver_out(driver)


@router.delete(
    "/{driver_id}",
    response_model=Message,
    dependencies=[Depends(require_role(Role.ROOT_ADMIN, Role.ADMIN))],
)
async def delete_driver(driver_id: str) -> Message:
    driver = await _get_or_404(driver_id)
    await driver.delete()
    return Message(detail="Driver deleted")
