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

router = APIRouter(prefix="/api/users", tags=["users"])

# `root_admin` has full, unrestricted CRUD over every user, including other root_admin/admin
# accounts. `admin` has a narrow, scoped exception to otherwise having no user-management
# access at all: it may create/view/edit/deactivate GUARDIAN-role accounts only, and cannot
# see, edit, or even detect the existence of root_admin/other-admin accounts (scoped lookups
# 404, not 403, so an admin probing another admin's id learns nothing). This is why the auth
# check moved from a router-level `dependencies=[...]` (whose return value isn't injectable)
# to a per-route `actor: User = Depends(...)` parameter — every handler needs to know *who* is
# calling, not just that the role is allowed to reach this router at all.
_ALLOWED_ROLES = (Role.ROOT_ADMIN, Role.ADMIN)


def _require_admin_scope(actor: User, target_role: Role) -> None:
    """Raise 404 if a non-root_admin actor (i.e. `admin`) is reaching outside its scope.

    404 rather than 403 is deliberate: an `admin` account must not be able to distinguish
    "that user doesn't exist" from "that user exists but isn't a guardian, none of your
    business" — either response would leak that other admin/root_admin accounts exist.
    """
    if actor.role == Role.ADMIN and target_role != Role.GUARDIAN:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")


@router.get("", response_model=list[UserOut])
async def list_users(actor: User = Depends(require_role(*_ALLOWED_ROLES))) -> list[UserOut]:
    if actor.role == Role.ADMIN:
        # An admin's user list only ever shows guardians — never root_admin/other admins.
        users = await User.find(User.role == Role.GUARDIAN).to_list()
    else:
        users = await User.find_all().to_list()
    return [user_out(u) for u in users]


@router.post("", response_model=UserOut, status_code=status.HTTP_201_CREATED)
async def create_user(
    body: UserCreate, actor: User = Depends(require_role(*_ALLOWED_ROLES))
) -> UserOut:
    if actor.role == Role.ADMIN and body.role != Role.GUARDIAN:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, "admin may only create guardian-role accounts"
        )
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
async def get_user(
    user_id: str, actor: User = Depends(require_role(*_ALLOWED_ROLES))
) -> UserOut:
    target = await _get_or_404(user_id)
    _require_admin_scope(actor, target.role)
    return user_out(target)


@router.put("/{user_id}", response_model=UserOut)
async def update_user(
    user_id: str, body: UserUpdate, actor: User = Depends(require_role(*_ALLOWED_ROLES))
) -> UserOut:
    target = await _get_or_404(user_id)
    _require_admin_scope(actor, target.role)
    data = body.model_dump(exclude_unset=True)
    if actor.role == Role.ADMIN and "role" in data and data["role"] != Role.GUARDIAN:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, "admin may only manage guardian-role accounts"
        )
    if "password" in data:
        target.password_hash = hash_secret(data.pop("password"))
    for field, value in data.items():
        setattr(target, field, value)
    await target.touch_and_save()
    return user_out(target)


@router.delete("/{user_id}", response_model=Message)
async def delete_user(
    user_id: str, actor: User = Depends(require_role(*_ALLOWED_ROLES))
) -> Message:
    target = await _get_or_404(user_id)
    _require_admin_scope(actor, target.role)
    await target.delete()
    return Message(detail="User deleted")
