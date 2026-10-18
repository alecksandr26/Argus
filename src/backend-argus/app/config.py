"""Centralized settings, read once from the environment (and `.env` if present).

Every field has a default so the app boots (and `pytest` runs) with no `.env` at all — matching
the "no required config file" convention `cv-argus`'s `constants.py` already follows in this
repo. Only `JWT_SECRET`'s default is unsafe to use past local dev; see `.env.example`.
"""
from __future__ import annotations

from typing import Literal

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

    # Off by default. When true, `app.main`'s lifespan calls `scripts.seed_dev_data.seed(reset=
    # False)` on every startup — additive/idempotent (skips anything that already exists by
    # email/plate_number/license_number), so it's safe to leave on permanently in a dev
    # `docker-compose.yml` without re-wiping the database on every restart. The destructive,
    # deterministic `python -m scripts.seed_dev_data` (which *does* wipe first) is unaffected by
    # this flag — that's still a manual, explicit action. Never enable in a real deployment.
    seed_demo_data: bool = False

    # Empty (disabled) by default. When set to a path, every demo seed run (the manual script or
    # the idempotent SEED_DEMO_DATA startup path above) additionally writes a plaintext
    # `role  email  password` line per seeded user to that path — a convenience for grabbing
    # test-login credentials without reading source, not a new secret: every password here is
    # the same fixed, already-public dev-only constant already printed to stdout and documented
    # in README.md. Never point this at a path a real deployment serves or a path that gets
    # committed.
    seed_credentials_file: str = ""

    # root_admin-only on/off control of the cloud fleet simulator (see app/simulator_control.py
    # and src/infra-argus). "disabled" (the default) makes every /api/simulator endpoint 503, so
    # local dev keeps using `docker compose --profile simulator` exactly as before. "ecs" starts
    # and stops one Fargate Spot task (simulator + OSRM containers) per session.
    simulator_control: Literal["disabled", "ecs"] = "disabled"
    simulator_ecs_cluster: str = ""
    simulator_task_definition: str = ""
    # Comma-separated, like CORS_ORIGINS.
    simulator_subnets: str = ""
    simulator_security_groups: str = ""
    # The URL the simulator task posts to. Empty (the default) means "this backend's own URL, as
    # seen on the start request" — see app/routers/simulator.py's `start_simulator`.
    simulator_backend_url: str = ""
    # Hard cost guardrails (src/infra-argus/README.md's R1/R3): every session is exactly this
    # long — the API accepts no duration — and the monthly quota is charged a full session up
    # front on every start.
    simulator_session_minutes: int = 10
    simulator_monthly_quota_minutes: int = 120

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def simulator_subnet_list(self) -> list[str]:
        return [s.strip() for s in self.simulator_subnets.split(",") if s.strip()]

    @property
    def simulator_security_group_list(self) -> list[str]:
        return [s.strip() for s in self.simulator_security_groups.split(",") if s.strip()]


settings = Settings()
