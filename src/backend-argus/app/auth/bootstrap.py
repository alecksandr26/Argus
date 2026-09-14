"""Root-admin bootstrap. See `CLAUDE.md`'s "Auth design" section for the full rationale.

Without this, there was no way to get the *first* `root_admin` into a real deployment at all —
`POST /api/users` (the only account-creation endpoint) already requires an existing `root_admin`
JWT to call, and `scripts/seed_dev_data.py` is a manual, destructive dev-only script nobody would
run against production. `ensure_root_admin()` closes that gap by running on every backend
startup (see `app.main`'s `lifespan`) and creating one root_admin from env vars if none exists
yet at the configured email.

Deliberately idempotent by *email*, not "any root_admin exists": re-running this on every
restart only ever inserts once. An existing user at `settings.root_admin_email` is left
completely untouched — including its role and password hash — even if the env vars change later,
so a deliberately-rotated password (or promoted/demoted role) is never silently reset just
because the container restarted.
"""
from __future__ import annotations

import hashlib
import logging

from app.auth.security import hash_secret
from app.config import settings
from app.models.common import Role
from app.models.user import User

logger = logging.getLogger(__name__)


async def ensure_root_admin() -> User | None:
    """Create the configured root_admin if no user exists at `settings.root_admin_email` yet.

    Returns the newly-created `User`, or `None` if one already existed (no-op). Computes
    `sha256(raw_password)` then `hash_secret()`s that digest — the exact two-step pipeline a real
    browser login produces (see `app/schemas/common.py`'s `Sha256HexDigest` docstring) — so
    logging in afterward with the raw `ROOT_ADMIN_PASSWORD` value actually succeeds.
    """
    existing = await User.find_one(User.email == settings.root_admin_email)
    if existing is not None:
        return None

    digest = hashlib.sha256(settings.root_admin_password.encode("utf-8")).hexdigest()
    user = User(
        email=settings.root_admin_email,
        password_hash=hash_secret(digest),
        role=Role.ROOT_ADMIN,
        first_name=settings.root_admin_first_name,
        last_name=settings.root_admin_last_name,
        phone_number=settings.root_admin_phone_number,
    )
    await user.insert()
    logger.warning("Bootstrapped root_admin user %s at startup.", user.email)
    return user
