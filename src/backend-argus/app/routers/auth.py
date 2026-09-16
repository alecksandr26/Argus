from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status

from app.auth.dependencies import get_current_user
from app.auth.security import create_access_token, hash_secret, verify_secret
from app.models.user import User
from app.schemas.auth import LoginRequest, LoginResponse, LoginUser, MeUpdate
from app.schemas.user import UserOut
from app.serializers import user_out

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/login", response_model=LoginResponse)
async def login(body: LoginRequest) -> LoginResponse:
    user = await User.find_one(User.email == body.email)
    if user is None or not user.is_active or not verify_secret(body.password, user.password_hash):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid email or password")

    user.last_login = datetime.now(timezone.utc)
    await user.save()

    token = create_access_token(subject=str(user.id), extra_claims={"role": user.role.value})
    return LoginResponse(
        access_token=token,
        user=LoginUser(
            id_user=str(user.id),
            email=user.email,
            role=user.role,
            first_name=user.first_name,
            last_name=user.last_name,
        ),
    )


@router.get("/me", response_model=UserOut)
async def get_me(user: User = Depends(get_current_user)) -> UserOut:
    """Self-service profile read — any authenticated role, no `/api/users` access needed."""
    return user_out(user)


@router.put("/me", response_model=UserOut)
async def update_me(body: MeUpdate, user: User = Depends(get_current_user)) -> UserOut:
    """Self-service profile edit — email/name/phone/password only (see `MeUpdate`'s doc
    comment for why role/is_active can't be sent here at all, structurally)."""
    data = body.model_dump(exclude_unset=True)
    if "email" in data and data["email"] != user.email:
        existing = await User.find_one(User.email == data["email"])
        if existing is not None and existing.id != user.id:
            raise HTTPException(status.HTTP_409_CONFLICT, "Email already registered")
    if "password" in data:
        user.password_hash = hash_secret(data.pop("password"))
    for field, value in data.items():
        setattr(user, field, value)
    await user.touch_and_save()
    return user_out(user)
