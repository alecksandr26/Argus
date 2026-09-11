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

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


settings = Settings()
