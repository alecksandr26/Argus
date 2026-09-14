"""Tests for `app.auth.bootstrap.ensure_root_admin` — see that module's docstring for the
rationale (there was previously no way to get a first root_admin into a real deployment)."""
import pytest

from app.auth.bootstrap import ensure_root_admin
from app.auth.security import verify_secret
from app.config import settings
from app.models.common import Role
from app.models.user import User
from tests.conftest import sha256_hex


@pytest.mark.asyncio
async def test_ensure_root_admin_creates_when_missing(mongo_client, monkeypatch):
    monkeypatch.setattr(settings, "root_admin_email", "bootstrap-admin@example.com")
    monkeypatch.setattr(settings, "root_admin_password", "s3cret-raw-password")

    created = await ensure_root_admin()

    assert created is not None
    assert created.email == "bootstrap-admin@example.com"
    assert created.role == Role.ROOT_ADMIN
    # The stored hash must be bcrypt(sha256(raw)) — the exact pipeline a real browser login
    # produces — so logging in afterward with the raw env-var password actually works.
    assert verify_secret(sha256_hex("s3cret-raw-password"), created.password_hash)

    from_db = await User.find_one(User.email == "bootstrap-admin@example.com")
    assert from_db is not None


@pytest.mark.asyncio
async def test_ensure_root_admin_is_idempotent(mongo_client, monkeypatch):
    monkeypatch.setattr(settings, "root_admin_email", "existing-admin@example.com")
    monkeypatch.setattr(settings, "root_admin_password", "whatever")

    existing = User(
        email="existing-admin@example.com",
        password_hash="not-touched",
        role=Role.GUARDIAN,
        first_name="Already",
        last_name="Here",
        phone_number="+1-555-9999",
    )
    await existing.insert()

    result = await ensure_root_admin()

    assert result is None
    unchanged = await User.find_one(User.email == "existing-admin@example.com")
    assert unchanged is not None
    assert unchanged.role == Role.GUARDIAN
    assert unchanged.password_hash == "not-touched"
