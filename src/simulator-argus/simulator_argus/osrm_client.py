"""Optional real road-following route geometry via a local OSRM instance.

Enabled by default (`SIMULATOR_USE_OSRM=true`), but it still needs a one-time setup step --
downloading and preprocessing a full Mexico OSM extract, see README's "Optional: real
road-following routes via OSRM" section -- before it can actually produce real geometry. When
disabled, or when a query fails for any reason (OSRM not running, extract not preprocessed yet,
network hiccup), callers get a plain two-point `[origin, destination]` polyline back instead --
the exact shape `virtual_truck.py` already knows how to walk via straight-line interpolation, so
OSRM being unavailable (including on a first run before the one-time setup script has been run)
never blocks the simulator from running.
"""
from __future__ import annotations

import logging
from typing import Optional

import httpx

logger = logging.getLogger(__name__)


async def fetch_route_geometry(
    osrm_base_url: str,
    origin: tuple[float, float],
    destination: tuple[float, float],
    timeout: float = 10.0,
    transport: Optional[httpx.AsyncBaseTransport] = None,
) -> list[tuple[float, float]]:
    """Returns a list of `(lat, lon)` points tracing the real road route from `origin` to
    `destination`, via OSRM's `/route/v1/driving` endpoint. Falls back to the two-point
    straight line `[origin, destination]` on any failure, never raises. `transport` is
    test-only -- injects a mocked transport instead of a real socket."""
    lon1, lat1 = origin[1], origin[0]
    lon2, lat2 = destination[1], destination[0]
    url = f"{osrm_base_url}/route/v1/driving/{lon1},{lat1};{lon2},{lat2}"
    params = {"overview": "full", "geometries": "geojson"}
    try:
        async with httpx.AsyncClient(timeout=timeout, transport=transport) as client:
            resp = await client.get(url, params=params)
            resp.raise_for_status()
            data = resp.json()
        coordinates = data["routes"][0]["geometry"]["coordinates"]  # [[lon, lat], ...]
        points = [(lat, lon) for lon, lat in coordinates]
        if len(points) < 2:
            raise ValueError("OSRM returned a degenerate route geometry")
        return points
    except Exception:
        logger.warning(
            "OSRM route query failed (%s -> %s); falling back to a straight line",
            origin,
            destination,
        )
        return [origin, destination]
