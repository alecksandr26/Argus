from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, status

from app.auth.security import create_access_token, verify_secret
from app.models.user import User
from app.schemas.auth import LoginRequest, LoginResponse, LoginUser

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
