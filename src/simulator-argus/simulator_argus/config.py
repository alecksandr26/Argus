"""Centralized settings, read once from the environment (and `.env` if present).

Mirrors `src/backend-argus/app/config.py`'s own convention: every field has a dev-safe default,
so the simulator runs against a fresh backend with zero required configuration.
"""
from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Docker-network hostname by default (see docker-compose.yml); override to
    # http://localhost:8000 when running this module against a host-run backend.
    backend_base_url: str = "http://backend-argus:8000"

    # root_admin credentials used ONLY once at startup, to provision the SIM-* fleet and mint
    # each truck's own device API key. Every ongoing status/alert write after that uses that
    # truck's own key, never these credentials again -- see CLAUDE.md's "Auth" section. Defaults
    # match backend-argus's own bootstrap defaults so this works out of the box.
    simulator_admin_email: str = "admin@argus.dev"
    simulator_admin_password: str = "changeme123"

    simulator_truck_count: int = 4
    simulator_status_interval_seconds: float = 5.0

    # How long a simulated trip takes, wall-clock -- from a few minutes (a fast demo) to several
    # hours (a more realistic shift), independent of the route's real road distance. Movement
    # always advances by elapsed-time-fraction of this window, never by a physically-consistent
    # distance/speed relationship -- see CLAUDE.md's "Movement model" section for why that's a
    # deliberate simplification, not an oversight.
    simulator_route_duration_minutes: float = 20.0

    # Per-truck scenario profile is drawn at random, weighted by these proportions -- e.g.
    # "normal=0.55,drowsy_escalation=0.25,panic=0.20". Unknown names or a malformed entry are
    # ignored; if nothing parses, falls back to an equal split across all known scenarios. See
    # scenarios.py's SCENARIOS for the valid names.
    simulator_scenario_weights: str = "normal=0.55,drowsy_escalation=0.25,panic=0.20"

    # Baseline probability of a "medium" vigilance blip on an otherwise-quiet tick (the `normal`
    # profile, and the calm stretches of `panic`) -- see scenarios.py.
    simulator_medium_blip_probability: float = 0.1

    # Deletes any SIM-* trucks/drivers/routes/alerts left over from a previous run before
    # provisioning fresh ones, so repeated `docker compose up`s during dev stay idempotent
    # instead of piling up ghost fleets.
    simulator_reset: bool = True

    # Off by default -- real road-following tracks need a one-time setup step (downloading and
    # preprocessing a full Mexico OSM extract; see README's "Optional: real road-following
    # routes via OSRM"). When true, fleet.py queries OSRM for each route's real driving geometry
    # at provisioning time; when false, or when that query fails for any reason (OSRM not
    # running, extract not preprocessed yet), virtual trucks fall back to a straight two-point
    # line between origin and destination -- see osrm_client.py.
    simulator_use_osrm: bool = False
    osrm_base_url: str = "http://osrm:5000"


settings = Settings()
