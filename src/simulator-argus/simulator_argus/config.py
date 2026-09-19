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

    simulator_truck_count: int = 6
    simulator_status_interval_seconds: float = 5.0

    # How long a simulated trip takes, wall-clock -- from a few minutes (a fast demo) to several
    # hours (a more realistic shift), independent of the route's real road distance. Movement
    # always advances by elapsed-time-fraction of this window, never by a physically-consistent
    # distance/speed relationship -- see CLAUDE.md's "Movement model" section for why that's a
    # deliberate simplification, not an oversight.
    simulator_route_duration_minutes: float = 20.0

    # Per-truck scenario profile is drawn at random, weighted by these proportions -- e.g.
    # "normal=0.45,drowsy_escalation=0.35,panic=0.20". Unknown names or a malformed entry are
    # ignored; if nothing parses, falls back to an equal split across all known scenarios. See
    # scenarios.py's SCENARIOS for the valid names. `drowsy_escalation` is weighted above its
    # earlier 0.25 because it's the only profile that fires *repeating* alerts (`normal` never
    # fires one, `panic` fires exactly one per truck lifetime) -- at the old 0.55/0.25/0.20 split
    # with 4 trucks, a typical run left only ~1 truck ever producing an Alert, making the Live
    # Ops alert feed look stuck/empty next to the constantly-updating vigilance tiles. `normal`
    # still stays the single largest slice on purpose, so the dashboard isn't wall-to-wall
    # incidents -- see CLAUDE.md's "Scenario design" section.
    simulator_scenario_weights: str = "normal=0.45,drowsy_escalation=0.35,panic=0.20"

    # Baseline probability of a "medium" vigilance blip on an otherwise-quiet tick (the `normal`
    # profile, and the calm stretches of `panic`) -- see scenarios.py.
    simulator_medium_blip_probability: float = 0.1

    # Deletes any SIM-* trucks/drivers/routes/alerts left over from a previous run before
    # provisioning fresh ones, so repeated `docker compose up`s during dev stay idempotent
    # instead of piling up ghost fleets.
    simulator_reset: bool = True

    # On by default -- real road-following tracks are the intended demo experience. This still
    # needs a one-time setup step to actually take effect (downloading and preprocessing a full
    # Mexico OSM extract; see README's "Optional: real road-following routes via OSRM"), which
    # can't itself be automated into `docker compose up` without either bundling a huge
    # preprocessed dataset into the repo or adding a slow first-run init step -- so until that
    # one-time script has been run, fleet.py's OSRM query fails (osrm not running, or the extract
    # not preprocessed yet) and virtual trucks safely fall back to a straight two-point line
    # between origin and destination -- see osrm_client.py.
    simulator_use_osrm: bool = True
    osrm_base_url: str = "http://osrm:5000"


settings = Settings()
