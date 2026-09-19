"""One instance per provisioned route -- the actual simulated traffic (movement + status pings +
scenario-driven alerts), running as its own asyncio task. See CLAUDE.md's "Movement model"
section for why the geometry is a real OSRM polyline only when opted into, and a synthetic
straight line otherwise.
"""
from __future__ import annotations

import asyncio
import logging
import math
import random
from datetime import datetime, timezone

from .client import BackendClient
from .fleet import VirtualTruckSpec
from .scenarios import build_scenario

logger = logging.getLogger(__name__)

# Realistic consumer-GPS wobble, not a random teleport: +-60m per axis stays comfortably under
# the smallest real per-tick forward progress across all route templates (~310m/tick for
# Monterrey<->Saltillo at default settings), so the dot still has natural noise but visibly
# advances instead of "spinning" tick to tick (each tick redraws jitter independently, so a
# magnitude comparable to or larger than per-tick progress reads as random wandering, not GPS
# noise on top of real movement).
_JITTER_METERS = 60.0
_METERS_PER_DEGREE = 111_320.0
_JITTER_DEGREES = _JITTER_METERS / _METERS_PER_DEGREE
_EARTH_RADIUS_METERS = 6_371_000.0


def _haversine_meters(a: tuple[float, float], b: tuple[float, float]) -> float:
    lat1, lon1, lat2, lon2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 2 * _EARTH_RADIUS_METERS * math.asin(min(1.0, math.sqrt(h)))


def _cumulative_distances(polyline: list[tuple[float, float]]) -> list[float]:
    cumulative = [0.0]
    for a, b in zip(polyline, polyline[1:]):
        cumulative.append(cumulative[-1] + _haversine_meters(a, b))
    return cumulative


def interpolate_along_polyline(
    polyline: list[tuple[float, float]], fraction: float
) -> tuple[float, float]:
    """Walks `polyline` (>= 2 points, `(lat, lon)`) to the point `fraction` of its total length
    along the way -- a straight-line lerp when `polyline` has exactly 2 points (the no-OSRM
    fallback shape), or a real road-following walk when it's a full OSRM geometry."""
    fraction = max(0.0, min(1.0, fraction))
    if len(polyline) < 2:
        lat, lon = polyline[0]
        return _jitter(lat, lon)

    cumulative = _cumulative_distances(polyline)
    total = cumulative[-1]
    if total <= 0:
        lat, lon = polyline[-1]
        return _jitter(lat, lon)

    target = fraction * total
    for i in range(1, len(cumulative)):
        if target <= cumulative[i]:
            segment_length = cumulative[i] - cumulative[i - 1]
            local_fraction = (target - cumulative[i - 1]) / segment_length if segment_length else 0.0
            a, b = polyline[i - 1], polyline[i]
            lat = a[0] + (b[0] - a[0]) * local_fraction
            lon = a[1] + (b[1] - a[1]) * local_fraction
            return _jitter(lat, lon)

    lat, lon = polyline[-1]
    return _jitter(lat, lon)


def _jitter(lat: float, lon: float) -> tuple[float, float]:
    return (
        lat + random.uniform(-_JITTER_DEGREES, _JITTER_DEGREES),
        lon + random.uniform(-_JITTER_DEGREES, _JITTER_DEGREES),
    )


class VirtualTruck:
    def __init__(
        self,
        client: BackendClient,
        spec: VirtualTruckSpec,
        interval_seconds: float,
        medium_blip_probability: float = 0.1,
    ) -> None:
        self._client = client
        self._spec = spec
        self._interval = interval_seconds
        total_seconds = (spec.arrival - spec.departure).total_seconds()
        total_ticks = max(1, int(total_seconds / interval_seconds)) if interval_seconds else 1
        self._scenario = build_scenario(
            spec.scenario, total_ticks, medium_blip_probability, interval_seconds
        )
        self._odometer = 0.0
        self._open_alert_id: str | None = None

    async def run(self) -> None:
        tick = 0
        while True:
            try:
                await self._tick(tick)
            except Exception:
                logger.exception(
                    "Truck %s: tick %d failed, will retry next interval",
                    self._spec.truck_id,
                    tick,
                )
            tick += 1
            await asyncio.sleep(self._interval)

    async def _tick(self, tick: int) -> None:
        spec = self._spec
        now = datetime.now(timezone.utc)
        total_seconds = (spec.arrival - spec.departure).total_seconds()
        elapsed_seconds = (now - spec.departure).total_seconds()
        fraction = elapsed_seconds / total_seconds if total_seconds > 0 else 1.0

        lat, lon = interpolate_along_polyline(spec.geometry, fraction)
        speed = 0.0 if fraction >= 1.0 else round(random.uniform(70.0, 100.0), 1)
        self._odometer += speed * (self._interval / 3600.0)
        vigilance = self._scenario.vigilance(tick)

        await self._client.post_status(
            spec.device_key,
            spec.route_id,
            {
                "current_coordinates": {"lat": lat, "lon": lon},
                "current_speed": speed,
                "odometer": round(self._odometer, 2),
                "vigilance": vigilance,
            },
        )

        event = self._scenario.alert(tick)
        if event is not None:
            alert = await self._client.post_alert(
                spec.device_key,
                {
                    "id_route": spec.route_id,
                    "alert_type": event.alert_type,
                    "severity_level": event.severity_level,
                    "source": event.source,
                    "ai_metadata": event.ai_metadata,
                    "grip_status": event.grip_status,
                    "coordinates": {"lat": lat, "lon": lon},
                    "speed_at_event": speed,
                },
            )
            self._open_alert_id = alert["id_alert"]
            logger.info(
                "Truck %s fired a %s %s alert on route %s",
                spec.truck_id,
                event.severity_level,
                event.source,
                spec.route_id,
            )

        if self._open_alert_id and self._scenario.should_resolve(tick):
            await self._client.resolve_alert(
                spec.device_key, self._open_alert_id, now.isoformat()
            )
            logger.info("Truck %s resolved alert %s", spec.truck_id, self._open_alert_id)
            self._open_alert_id = None
