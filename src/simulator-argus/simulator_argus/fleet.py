"""Provisions the simulator's own SIM-* drivers/trucks/routes via the admin API at startup, and
mints each truck's device API key -- see CLAUDE.md's "Data: self-provisioned, not SEED_DEMO_DATA"
section for why this doesn't just ride on backend-argus's seed script instead.
"""
from __future__ import annotations

import itertools
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from .client import BackendClient
from .config import Settings
from .osrm_client import fetch_route_geometry
from .scenarios import choose_scenario_name, parse_scenario_weights

logger = logging.getLogger(__name__)

SIM_PREFIX = "SIM-"

# Real Mexican highway city pairs, matching the flavor backend-argus/scripts/seed_dev_data.py
# already uses. The (lat, lon) origin of each pair is used ONLY client-side, to compute a route
# geometry to walk (either OSRM's real road-following polyline, or a straight-line fallback) --
# Route itself stores no origin coordinate field (app/models/route.py), so this is never sent to
# the backend as-is.
ROUTE_TEMPLATES: list[tuple[str, str, tuple[float, float], tuple[float, float]]] = [
    ("Ciudad de México", "Guadalajara", (19.4326, -99.1332), (20.6597, -103.3496)),
    ("Monterrey", "Saltillo", (25.6866, -100.3161), (25.4260, -100.9959)),
    ("Puebla", "Veracruz", (19.0414, -98.2063), (19.1738, -96.1342)),
    ("Tijuana", "Mexicali", (32.5149, -117.0382), (32.6245, -115.4523)),
]


@dataclass(frozen=True)
class VirtualTruckSpec:
    route_id: str
    truck_id: str
    device_key: str
    geometry: list[tuple[float, float]]
    departure: datetime
    arrival: datetime
    scenario: str


def _is_sim(natural_key: str) -> bool:
    return natural_key.startswith(SIM_PREFIX)


async def reset_previous_run(client: BackendClient, token: str) -> None:
    """Deletes every SIM-* truck/driver/route (and their alerts) from a previous run, so
    repeated `docker compose up`s during dev stay idempotent instead of piling up ghost fleets.
    Identifies "ours" by the SIM- prefix on plate_number/license_number, since Route itself
    carries no distinguishing text of its own -- routes are matched via their id_truck/id_driver.
    """
    trucks = await client.list_trucks(token)
    drivers = await client.list_drivers(token)
    routes = await client.list_routes(token)

    sim_truck_ids = {t["id_truck"] for t in trucks if _is_sim(t["plate_number"])}
    sim_driver_ids = {d["id_driver"] for d in drivers if _is_sim(d["license_number"])}
    sim_routes = [
        r
        for r in routes
        if r["id_truck"] in sim_truck_ids or r["id_driver"] in sim_driver_ids
    ]

    for route in sim_routes:
        for alert in await client.list_alerts(token, route_id=route["id_route"]):
            await client.delete_alert(token, alert["id_alert"])
        await client.delete_route(token, route["id_route"])

    for truck_id in sim_truck_ids:
        await client.delete_truck(token, truck_id)
    for driver_id in sim_driver_ids:
        await client.delete_driver(token, driver_id)

    if sim_routes or sim_truck_ids or sim_driver_ids:
        logger.info(
            "Reset previous run: removed %d route(s), %d truck(s), %d driver(s)",
            len(sim_routes),
            len(sim_truck_ids),
            len(sim_driver_ids),
        )


async def provision_fleet(client: BackendClient, settings: Settings) -> list[VirtualTruckSpec]:
    """Logs in as root_admin ONCE, to create the fleet and mint each truck's own device key --
    every subsequent status/alert write uses that truck's key instead, never this admin token
    again. See CLAUDE.md's "Auth" section for why this scoping matters and how it's enforced."""
    token = await client.login(settings.simulator_admin_email, settings.simulator_admin_password)

    if settings.simulator_reset:
        await reset_previous_run(client, token)

    weights = parse_scenario_weights(settings.simulator_scenario_weights)

    specs: list[VirtualTruckSpec] = []
    templates = list(
        itertools.islice(itertools.cycle(ROUTE_TEMPLATES), settings.simulator_truck_count)
    )
    now = datetime.now(timezone.utc)
    route_duration = timedelta(minutes=settings.simulator_route_duration_minutes)

    for index, (origin_name, destination_name, origin, destination) in enumerate(templates):
        suffix = f"{index + 1:03d}"

        driver = await client.create_driver(
            token,
            {
                "first_name": "Simulated",
                "last_name": f"Driver {suffix}",
                "license_number": f"{SIM_PREFIX}LIC-{suffix}",
                "license_expiration": "2030-01-01",
                "phone_number": "+52-000-000-0000",
                "emergency_contact_name": "Argus Simulator",
                "emergency_contact_phone": "+52-000-000-0000",
                "blood_type": "O+",
                "operative_status": "on_route",
            },
        )
        truck = await client.create_truck(
            token,
            {
                "plate_number": f"{SIM_PREFIX}{suffix}",
                "brand": "Simulated",
                "model": "Virtual Fleet",
                "company_number": suffix,
                "operative_status": "active",
            },
        )

        departure = now
        arrival = now + route_duration
        route = await client.create_route(
            token,
            {
                "id_driver": driver["id_driver"],
                "id_truck": truck["id_truck"],
                "origin_name": origin_name,
                "destination_name": destination_name,
                "destination_coordinates": {"lat": destination[0], "lon": destination[1]},
                "estimated_departure": departure.isoformat(),
                "estimated_arrival": arrival.isoformat(),
                "operative_status": "in_progress",
            },
        )

        device_key = await client.rotate_key(token, truck["id_truck"])
        scenario = choose_scenario_name(weights)

        if settings.simulator_use_osrm:
            geometry = await fetch_route_geometry(settings.osrm_base_url, origin, destination)
        else:
            geometry = [origin, destination]

        specs.append(
            VirtualTruckSpec(
                route_id=route["id_route"],
                truck_id=truck["id_truck"],
                device_key=device_key,
                geometry=geometry,
                departure=departure,
                arrival=arrival,
                scenario=scenario,
            )
        )
        logger.info(
            "Provisioned %s: %s -> %s (%s, %d-point geometry)",
            truck["plate_number"],
            origin_name,
            destination_name,
            scenario,
            len(geometry),
        )

    return specs
