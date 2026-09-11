from __future__ import annotations

from beanie import PydanticObjectId
from fastapi import APIRouter, Depends, HTTPException, status

from app.auth.dependencies import require_role
from app.auth.security import hash_secret
from app.models.common import Role
from app.models.user import User
from app.schemas.common import Message
from app.schemas.user import UserCreate, UserOut, UserUpdate
from app.serializers import user_out

router = APIRouter(
    prefix="/api/users",
    tags=["users"],
    dependencies=[Depends(require_role(Role.ROOT_ADMIN))],
)


@router.get("", response_model=list[UserOut])
async def list_users() -> list[UserOut]:
    return [user_out(u) for u in await User.find_all().to_list()]


@router.post("", response_model=UserOut, status_code=status.HTTP_201_CREATED)
async def create_user(body: UserCreate) -> UserOut:
    if await User.find_one(User.email == body.email) is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Email already registered")
    user = User(
        email=body.email,
        password_hash=hash_secret(body.password),
        role=body.role,
        first_name=body.first_name,
        last_name=body.last_name,
        phone_number=body.phone_number,
        is_active=body.is_active,
    )
    await user.insert()
    return user_out(user)


async def _get_or_404(user_id: str) -> User:
    try:
        oid = PydanticObjectId(user_id)
    except Exception:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
    user = await User.get(oid)
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
    return user


@router.get("/{user_id}", response_model=UserOut)
async def get_user(user_id: str) -> UserOut:
    return user_out(await _get_or_404(user_id))


@router.put("/{user_id}", response_model=UserOut)
async def update_user(user_id: str, body: UserUpdate) -> UserOut:
    user = await _get_or_404(user_id)
    data = body.model_dump(exclude_unset=True)
    if "password" in data:
        user.password_hash = hash_secret(data.pop("password"))
    for field, value in data.items():
        setattr(user, field, value)
    await user.touch_and_save()
    return user_out(user)


@router.delete("/{user_id}", response_model=Message)
async def delete_user(user_id: str) -> Message:
    user = await _get_or_404(user_id)
    await user.delete()
    return Message(detail="User deleted")
