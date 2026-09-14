"""Centralized settings, read once from the environment (and `.env` if present).

Every field has a default so the app boots (and `pytest` runs) with no `.env` at all — matching
the "no required config file" convention `cv-argus`'s `constants.py` already follows in this
repo. Only `JWT_SECRET`'s default is unsafe to use past local dev; see `.env.example`.
"""
from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    mongo_uri: str = "mongodb://localhost:27017"
    mongo_db: str = "argus"

    jwt_secret: str = "dev-secret-change-me"
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 480

    # Comma-separated in the environment; split into a list for FastAPI's CORSMiddleware.
    cors_origins: str = "http://localhost:5173"

    # The very first root_admin, auto-created on startup if no user exists at this email yet
    # (see app/auth/bootstrap.py) — otherwise there's no way to get a first admin into a real
    # deployment at all, short of manually running scripts/seed_dev_data.py. Defaults match that
    # script's own dev credentials so a bare `docker compose up` and a seed-script run agree.
    # `root_admin_password` is the *raw* password, same as a human would type into the login
    # form — unsafe past local dev, same caveat as `jwt_secret` above.
    root_admin_email: str = "admin@argus.dev"
    root_admin_password: str = "changeme123"
    root_admin_first_name: str = "Root"
    root_admin_last_name: str = "Admin"
    root_admin_phone_number: str = "+00-000-0000"

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


settings = Settings()
