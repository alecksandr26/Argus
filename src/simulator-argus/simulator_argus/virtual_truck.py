"""One instance per provisioned route -- the actual simulated traffic, running as its own asyncio
task. It plays both devices of a real truck:

- the **Pi**: its scenario holds a severity per tick, and `EpisodeTracker` turns severity changes
  into events (one alert per incident, at most one critical upgrade, one driver OK);
- the **ESP32**: one GPS point per second, and one combined report per POST to
  `/api/devices/me/report` -- sent at once when an event is pending, otherwise 5 s after the
  previous report ended -- with the ESP32's priority, retry windows and dead-letter rules
  (`delivery.DeliveryQueue`).

`run()` keeps the two halves in two asyncio tasks, like the ESP32's GPS loop and its
`senderTask`: the GPS/scenario tick reads a point every second while a report is in flight (an
attempt can take up to 8 s), so a slow or hanging backend doesn't stretch the scenario's timing
or leave extra holes in the path. `step()` runs one tick and then the sender inline, for tests.

Every attempt logs one line tagged `[live]` or `[dlq]` (attempt, transport, status, duration,
result), and so does every event the device gives up on (dropped, dead-lettered, archived),
whichever path gave up on it.

See CLAUDE.md's "Movement model" section for why the geometry is a real OSRM polyline only when
opted into, and a synthetic straight line otherwise.
"""
from __future__ import annotations

import asyncio
import logging
import math
import random
import time
from datetime import datetime, timezone
from typing import Callable, Optional

import httpx

from .client import BackendClient
from .constants import (
    ACTIVE_ROUTE_RETRY_S,
    GPS_READ_INTERVAL_S,
    NORMAL_BLIP_CHANCE,
    SIMULATOR_REPORT_INTERVAL_S,
)
from .delivery import DeliveryQueue, Finished, QueuedEvent
from .disruptions import (
    GPS_LOSS_SECONDS,
    OUTAGE_SECONDS,
    in_window,
    plan_window,
    plan_window_around,
)
from .events import EpisodeTracker, vigilance_for
from .fleet import VirtualTruckSpec
from .report import (
    GpsPoint,
    PointBuffer,
    ReportOutcome,
    build_report,
    classify_response,
    classify_transport_error,
)
from .scenarios import build_scenario

logger = logging.getLogger(__name__)

# Realistic consumer-GPS wobble, not a random teleport. Points are 1 s apart now (one per GPS
# read) and the route view draws them as a path, so the wobble has to stay well under the
# smallest per-second progress across all route templates (~60 m/s for Monterrey<->Saltillo at
# default settings): each point redraws jitter independently, and a magnitude comparable to the
# progress between points draws a zig-zag instead of a road. (It was +-60 m when only one point
# went out every 5 s.)
_JITTER_METERS = 8.0
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


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class VirtualTruck:
    def __init__(
        self,
        client: BackendClient,
        spec: VirtualTruckSpec,
        report_interval_seconds: float = SIMULATOR_REPORT_INTERVAL_S,
        blip_chance: float = NORMAL_BLIP_CHANCE,
        tick_seconds: float = GPS_READ_INTERVAL_S,
        clock: Callable[[], float] = time.monotonic,
        wall_clock: Callable[[], datetime] = _utcnow,
        outage_chance: float = 0.0,
        gps_loss_chance: float = 0.0,
        outage_around_critical: bool = False,
    ) -> None:
        self._client = client
        self._spec = spec
        self._report_interval = report_interval_seconds
        self._tick_seconds = tick_seconds if tick_seconds > 0 else GPS_READ_INTERVAL_S
        self._clock = clock
        self._wall_clock = wall_clock
        total_seconds = (spec.arrival - spec.departure).total_seconds()
        total_ticks = max(1, int(total_seconds / self._tick_seconds))
        self._scenario = build_scenario(
            spec.scenario, total_ticks, blip_chance, self._tick_seconds
        )
        self._tracker = EpisodeTracker()
        self._queue = DeliveryQueue()
        self._points = PointBuffer()
        self._last_report_end = clock()
        # Before the first 200 the active route is *unknown*, not null (C2.3 [sync 1]): points
        # go every report interval until a 200 says otherwise.
        self._route_known = False
        self._active_route: Optional[str] = None
        self.finished = False
        # Optional demo disruptions (disruptions.py), as `[start, end)` tick windows or None.
        critical_tick = getattr(self._scenario, "first_critical_tick", None)
        if outage_around_critical and critical_tick is not None:
            self._outage = plan_window_around(
                outage_chance, critical_tick, total_ticks, tick_seconds=self._tick_seconds
            )
        else:
            self._outage = plan_window(
                outage_chance, total_ticks, OUTAGE_SECONDS, self._tick_seconds
            )
        self._gps_loss = plan_window(
            gps_loss_chance, total_ticks, GPS_LOSS_SECONDS, self._tick_seconds
        )
        if self._outage or self._gps_loss:
            logger.info(
                "Truck %s: simulated network outage ticks %s, GPS loss ticks %s",
                spec.truck_id,
                self._outage,
                self._gps_loss,
            )
        self._tick = 0
        self._last_fix: Optional[tuple[float, float, float]] = None  # (t, lat, lon)

    @property
    def queue(self) -> DeliveryQueue:
        return self._queue

    @property
    def active_route(self) -> Optional[str]:
        return self._active_route

    async def run(self) -> None:
        """Ticks once per GPS read until the route ends, with the sender in its own task."""
        self._wake = asyncio.Event()
        sender = asyncio.create_task(self._sender_loop())
        try:
            tick = 0
            while not self.finished:
                try:
                    self._read(tick)
                except Exception:
                    logger.exception(
                        "Truck %s: tick %d failed, will retry next tick", self._spec.truck_id, tick
                    )
                self._wake.set()
                tick += 1
                await asyncio.sleep(self._tick_seconds)
        finally:
            if self.finished:
                self._wake.set()
                await sender
            else:  # cancelled (shutdown): don't leave the sender behind
                sender.cancel()
                await asyncio.gather(sender, return_exceptions=True)
        logger.info(
            "Truck %s: route %s has ended (stopped or completed); the truck stops reporting",
            self._spec.truck_id,
            self._spec.route_id,
        )

    async def _sender_loop(self) -> None:
        """Wakes on every GPS tick (or at once, when the previous report outlasted a tick) and
        sends whatever is due."""
        while not self.finished:
            await self._wake.wait()
            self._wake.clear()
            if self.finished:
                break
            try:
                await self._maybe_send(self._clock())
            except Exception:
                logger.exception("Truck %s: sending failed, will retry", self._spec.truck_id)

    async def step(self, tick: int) -> None:
        """One GPS read and then the sender, inline (what `run()` does in two tasks)."""
        now = self._read(tick)
        await self._maybe_send(now)

    def _read(self, tick: int) -> float:
        """One GPS read: a point, the scenario's reading, maybe a new live event."""
        self._tick = tick
        now = self._clock()
        reading = self._scenario.reading(tick)
        vigilance = vigilance_for(reading.severity)
        position: Optional[dict]
        event_speed: Optional[float]
        if not in_window(self._gps_loss, tick):
            lat, lon, speed = self._position()
            self._last_fix = (now, lat, lon)
            self._points.add(GpsPoint(now, lat, lon, speed, vigilance))
            position = {"lat": lat, "lon": lon, "last_known": False, "location_age_s": 0}
            event_speed = speed
        elif self._last_fix is not None:
            # No fix: the last good position, marked last known, with its age (C2.1); points
            # carry speed 0, events speed null (C1.8 [sync 2]).
            fix_t, lat, lon = self._last_fix
            age = round(max(0.0, now - fix_t), 1)
            self._points.add(GpsPoint(now, lat, lon, 0.0, vigilance, True, age))
            position = {"lat": lat, "lon": lon, "last_known": True, "location_age_s": age}
            event_speed = None
        else:  # no fix yet at all: no point, and events go with position unknown
            position, event_speed = None, None

        event = self._tracker.update(reading, now, position=position, speed=event_speed)
        if event is not None:
            self._queue.add_live(event, now)
            logger.info(
                "Truck %s: new %s event %s (episode %s)",
                self._spec.truck_id,
                event.priority_key,
                event.alert_type or "driver_ok",
                event.episode,
            )
        return now

    def _position(self) -> tuple[float, float, float]:
        spec = self._spec
        total_seconds = (spec.arrival - spec.departure).total_seconds()
        elapsed_seconds = (self._wall_clock() - spec.departure).total_seconds()
        fraction = elapsed_seconds / total_seconds if total_seconds > 0 else 1.0
        lat, lon = interpolate_along_polyline(spec.geometry, fraction)
        speed = 0.0 if fraction >= 1.0 else round(random.uniform(70.0, 100.0), 1)
        return lat, lon, speed

    def _points_interval(self) -> float:
        if self._route_known and self._active_route is None:
            return ACTIVE_ROUTE_RETRY_S
        return self._report_interval

    async def _maybe_send(self, now: float) -> None:
        item = self._queue.next_live(now)
        self._log_notices()  # what expiring the live windows just gave up on
        due = now - self._last_report_end >= self._points_interval()
        if item is None and due:
            # A dead-letter replay rides on a regular report, and only while no live event is
            # pending (next_dlq() checks that), so a replay never delays a live event.
            item = self._queue.next_dlq()
        if item is None and not due:
            return
        await self._send(item)

    async def _send(self, item: Optional[QueuedEvent]) -> ReportOutcome:
        points = self._points.drain()  # dropped whatever the outcome (plan section 1)
        had_event = item is not None
        tag = "[dlq]" if had_event and item.in_dlq else "[live]"
        if not had_event:
            attempt = 0
        elif item.in_dlq:
            attempt = item.attempts + 1
        else:
            attempt = item.live_failures + 1

        started = self._clock()
        body = build_report(points, item.to_wire(started) if had_event else None, started)
        transport = "http"
        if in_window(self._outage, self._tick):
            # Simulated lost connection: the attempt fails before reaching the backend.
            transport = "none"
            outcome = classify_transport_error("no_network", had_event)
        else:
            try:
                response = await self._client.post_report(self._spec.device_key, body)
                outcome = classify_response(response.status_code, response.body, had_event)
            except httpx.TimeoutException:
                outcome = classify_transport_error("timeout", had_event)
            except httpx.TransportError:
                outcome = classify_transport_error("transport_error", had_event)
        ended = self._clock()
        self._last_report_end = ended

        result = outcome.event_result or ("ok" if outcome.http_status == 200 else "failed")
        if outcome.event_reason:
            result = f"{result}:{outcome.event_reason}"
        elif transport == "none":
            result = f"{result}:no_network"
        log = logger.info if had_event or outcome.http_status != 200 else logger.debug
        log(
            "%s truck=%s event=%s attempt=%d transport=%s status=%s duration=%.2fs "
            "points=%d result=%s",
            tag,
            self._spec.truck_id,
            f"{item.event.priority_key}/{item.event.alert_type or 'driver_ok'}" if had_event else "-",
            attempt,
            transport,
            outcome.http_status if outcome.http_status is not None else "-",
            ended - started,
            len(points),
            result,
        )
        # After the attempt's own line, so a give-up it caused is logged right after it.
        self._apply(item, outcome, ended)
        return outcome

    def _apply(self, item: Optional[QueuedEvent], outcome: ReportOutcome, now: float) -> None:
        if outcome.route_known:
            self._route_known = True
            self._active_route = outcome.active_route
        if outcome.reconfigure:
            # A real ESP32 would re-CONFIG from the Pi; the simulator's key only changes if the
            # truck was deleted or its key rotated, so all it can do is say so.
            logger.warning(
                "Truck %s: device key rejected (401); was the truck deleted or its key rotated?",
                self._spec.truck_id,
            )
        if item is not None:
            if outcome.event_result == "delivered":
                self._queue.mark_delivered(item)
            elif outcome.event_result == "rejected":
                self._queue.mark_rejected(item, outcome.event_reason or "invalid")
            elif outcome.event_result == "failed":
                self._queue.mark_failed(item, outcome.event_reason or "failed", now)
        self._log_notices()
        if outcome.route_finished:
            self.finished = True

    def _log_notices(self) -> None:
        for notice in self._queue.drain_notices():
            self._log_notice(notice)

    def _log_notice(self, notice: Finished) -> None:
        """One line per event the device gave up on, in the attempt line's format with no
        transport/status: `result=<outcome>:<reason>` (`dropped:retry_window`,
        `dead_lettered:http_503`, `archived:max_attempts`, `archived:route_finished`, ...)."""
        item = notice.item
        # A live event ends in the live log (dropped, dead-lettered, rejected while live); one
        # that had already reached the DLQ ends in the DLQ log.
        in_dlq = item.in_dlq and notice.outcome != "dead_lettered"
        logger.warning(
            "%s truck=%s event=%s attempt=%d transport=- status=- duration=0.00s points=0 "
            "result=%s:%s",
            "[dlq]" if in_dlq else "[live]",
            self._spec.truck_id,
            f"{item.event.priority_key}/{item.event.alert_type or 'driver_ok'}",
            item.attempts if in_dlq else item.live_failures,
            notice.outcome,
            notice.reason,
        )
