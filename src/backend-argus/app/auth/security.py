"""Password and device-key hashing (bcrypt, called directly) + JWT issuance/validation (PyJWT).

`passlib` is deliberately not used here — it's unmaintained (no release since 2020) and its
bcrypt backend breaks under `bcrypt>=4.1` (it probes for a `__about__` attribute that recent
bcrypt releases removed). Calling `bcrypt.hashpw`/`bcrypt.checkpw` directly avoids that failure
mode for one extra line of code. Similarly, `PyJWT` is used instead of `python-jose` — the
latter's upstream is unmaintained with a history of CVEs; PyJWT is the actively-maintained,
narrower-scope library FastAPI's own current docs point to.
"""
from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import bcrypt
import jwt

from app.config import settings


def hash_secret(plaintext: str) -> str:
    """Used for both user passwords and truck device API keys — same primitive, same cost
    factor; there's no reason for these to use different hashing schemes."""
    return bcrypt.hashpw(plaintext.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_secret(plaintext: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(plaintext.encode("utf-8"), hashed.encode("utf-8"))
    except ValueError:
        # Malformed/empty stored hash (e.g. a truck that never had a key issued) -> never match.
        return False


def generate_device_api_key() -> str:
    """A per-truck static key, generated once and shown once (GitHub-PAT-style) — see the
    backend CLAUDE.md's "Auth design" section for why devices don't get a login/refresh flow."""
    return secrets.token_urlsafe(32)


def create_access_token(subject: str, extra_claims: Optional[dict[str, Any]] = None) -> str:
    now = datetime.now(timezone.utc)
    payload: dict[str, Any] = {
        "sub": subject,
        "iat": now,
        "exp": now + timedelta(minutes=settings.jwt_expire_minutes),
    }
    if extra_claims:
        payload.update(extra_claims)
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_access_token(token: str) -> Optional[dict[str, Any]]:
    try:
        return jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
    except jwt.PyJWTError:
        return None
