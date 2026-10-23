"""Named per-virtual-truck behaviour profiles.

A scenario plays the part of the Pi's rule engine: once per tick (one GPS read, 1 s) it says
which severity the driver is held at and why (`Reading`). It never decides *events*: the
virtual truck feeds the readings to `events.EpisodeTracker`, which applies the same
transition rule the real devices use (one medium alert per incident, at most one critical
upgrade, one driver OK on recovery). So whatever a scenario does, the backend sees exactly the
event shapes a real truck would send.

Vigilance is scripted per profile, not pure randomness, so a demo run tells a coherent story
instead of an undifferentiated wall of severities. *Which* profile a truck gets, and exactly when
within its run a scripted episode lands, is randomized and scaled to the route's configured
duration -- see CLAUDE.md's "Scenario design" section.

Reasons are the Pi rule engine's own (`src/cv-argus/src/orchestrator/parts_rules.py`):
`head_down` and `eyes_closed` can be medium, `eyes_closed` and `no_face` critical.
"""
from __future__ import annotations

import logging
import random
from typing import Optional

from .constants import GPS_READ_INTERVAL_S, NORMAL_BLIP_CHANCE
from .events import Reading

logger = logging.getLogger(__name__)

_OK = Reading("low", "ok")


class Scenario:
    """`normal`: the driver stays fine (`low`) for the whole route, except for **at most one**
    `medium` blip: a glance down (`head_down`) that holds for a randomized 30 s-2 min dwell, then
    recovers. Whether the route gets its blip is a single draw with probability `blip_chance`
    (`SIMULATOR_NORMAL_BLIP_CHANCE`, 0.5 by default), and when it starts is uniform over the
    route (it always ends before the route does). The blip is one incident: one medium alert
    when it starts, one driver OK when it ends.

    Why a per-route budget and not a rate: a medium alert keeps the route's map colour until a
    guardian closes the incident or an hour of OK points passes (contracts C3.5), so any steady
    blip rate eventually turns every route medium. With one blip at most, about half of the
    normal routes stay fine from start to end."""

    name = "normal"

    _MEDIUM_DWELL_SECONDS_MIN = 30.0
    _MEDIUM_DWELL_SECONDS_MAX = 120.0
    _BLIP = Reading("medium", "head_down", drowsy_score=0.31)

    def __init__(
        self,
        total_ticks: int,
        blip_chance: float = NORMAL_BLIP_CHANCE,
        tick_seconds: float = GPS_READ_INTERVAL_S,
    ) -> None:
        self._total_ticks = max(total_ticks, 1)
        self._tick_seconds = tick_seconds if tick_seconds and tick_seconds > 0 else 1.0
        self._blip: Optional[tuple[int, int]] = self._plan_blip(blip_chance)

    @property
    def blip_window(self) -> Optional[tuple[int, int]]:
        """`(first tick, first tick after)` of the route's one blip, or `None`."""
        return self._blip

    def _ticks(self, seconds: float) -> int:
        return max(1, round(seconds / self._tick_seconds))

    def _plan_blip(self, chance: float) -> Optional[tuple[int, int]]:
        chance = min(1.0, max(0.0, chance))
        if chance <= 0.0 or random.random() >= chance:
            return None
        dwell = self._ticks(
            random.uniform(self._MEDIUM_DWELL_SECONDS_MIN, self._MEDIUM_DWELL_SECONDS_MAX)
        )
        # Keep a tick of fine driving on both sides, so the alert and its driver OK both happen
        # while the route is still running, even on a very short demo route.
        dwell = max(1, min(dwell, self._total_ticks - 2))
        start = random.randint(1, max(1, self._total_ticks - dwell - 1))
        return start, start + dwell

    def reading(self, tick: int) -> Reading:
        if self._blip is not None and self._blip[0] <= tick < self._blip[1]:
            return self._BLIP
        return _OK


class DrowsyEscalationScenario(Scenario):
    """Drifts `low -> medium -> critical -> low` on a cycle scaled to the route's duration: eyes
    closing longer and longer (`eyes_closed`). Through `EpisodeTracker` that is one medium
    alert, one critical upgrade in the same episode (the backend links it to the medium via
    `related_alert_id`), and one driver OK on recovery. The cycle repeats for as long as the
    route runs; with the default 20 min route the next cycle starts within the backend's 15 min
    incident window, so a guardian sees the repeats grouped under the same incident unless it
    was closed in between."""

    name = "drowsy_escalation"

    _ESCALATE_FRACTION = 0.15
    _PEAK_FRACTION = 0.30
    _RECOVER_FRACTION = 0.55
    _MIN_CYCLE_TICKS = 8
    _MEDIUM = Reading("medium", "eyes_closed", drowsy_score=0.58)
    _CRITICAL = Reading("critical", "eyes_closed", drowsy_score=0.88)

    def __init__(
        self,
        total_ticks: int,
        blip_chance: float = NORMAL_BLIP_CHANCE,
        tick_seconds: float = GPS_READ_INTERVAL_S,
    ) -> None:
        # Its own cycle is the story; `normal`'s blip doesn't apply.
        super().__init__(total_ticks, 0.0, tick_seconds)
        cycle = max(self._MIN_CYCLE_TICKS, self._total_ticks // 2)
        self._escalate_at = max(1, round(cycle * self._ESCALATE_FRACTION))
        self._peak_at = max(self._escalate_at + 1, round(cycle * self._PEAK_FRACTION))
        self._recover_at = max(self._peak_at + 1, round(cycle * self._RECOVER_FRACTION))
        self._cycle_length = self._recover_at + max(2, round(cycle * 0.2))

    def reading(self, tick: int) -> Reading:
        cycle = tick % self._cycle_length
        if cycle < self._escalate_at:
            return _OK
        if cycle < self._peak_at:
            return self._MEDIUM
        if cycle < self._recover_at:
            return self._CRITICAL
        return _OK


class NoFaceScenario(Scenario):
    """The driver leaves the camera's view once, at a random moment in the first third of the
    route, for a randomized 30-90 s: the Pi's no-face rule goes straight to `critical` with no
    medium step before it (`RULE_NO_FACE_CRITICAL_S`), and its scores are unknown (no face, no
    model output). Then the driver is back and recovers (one driver OK). That one incident is the
    whole story: the rest of the route is fine, with no `normal` blip on top (a medium after the
    critical would only re-colour a route that is already critical). Replaces the retired
    `panic` profile: the panic button is gone from the hardware."""

    name = "no_face"

    _AWAY_SECONDS_MIN = 30.0
    _AWAY_SECONDS_MAX = 90.0
    _AWAY = Reading("critical", "no_face", drowsy_score=None)

    def __init__(
        self,
        total_ticks: int,
        blip_chance: float = NORMAL_BLIP_CHANCE,
        tick_seconds: float = GPS_READ_INTERVAL_S,
    ) -> None:
        super().__init__(total_ticks, 0.0, tick_seconds)
        window = max(2, self._total_ticks // 3)
        self._away_from = random.randint(1, window)
        away = random.uniform(self._AWAY_SECONDS_MIN, self._AWAY_SECONDS_MAX)
        self._away_until = self._away_from + self._ticks(away)

    def reading(self, tick: int) -> Reading:
        if self._away_from <= tick < self._away_until:
            return self._AWAY
        return _OK


SCENARIOS: dict[str, type[Scenario]] = {
    "normal": Scenario,
    "drowsy_escalation": DrowsyEscalationScenario,
    "no_face": NoFaceScenario,
}

# Old names still accepted in `SIMULATOR_SCENARIO_WEIGHTS`, so a stack that still passes the
# pre-final-tweaks default (`...,panic=0.20`) keeps its weights.
LEGACY_SCENARIO_NAMES = {"panic": "no_face"}

_DEFAULT_WEIGHTS = {name: 1.0 for name in SCENARIOS}


def parse_scenario_weights(spec: str) -> dict[str, float]:
    """Parses `"normal=0.45,drowsy_escalation=0.35,no_face=0.20"` into a weight dict. Legacy
    names map to their replacement (`panic` -> `no_face`); unknown names and malformed entries
    are dropped; if nothing valid parses, falls back to an equal split across every known
    scenario so a typo'd `SIMULATOR_SCENARIO_WEIGHTS` never crashes provisioning."""
    weights: dict[str, float] = {}
    for entry in spec.split(","):
        entry = entry.strip()
        if not entry or "=" not in entry:
            continue
        name, _, raw_value = entry.partition("=")
        name = name.strip()
        if name in LEGACY_SCENARIO_NAMES:
            logger.warning(
                "Scenario %r is retired; using %r instead", name, LEGACY_SCENARIO_NAMES[name]
            )
            name = LEGACY_SCENARIO_NAMES[name]
        if name not in SCENARIOS:
            continue
        try:
            value = float(raw_value.strip())
        except ValueError:
            continue
        if value > 0:
            weights[name] = weights.get(name, 0.0) + value
    return weights or dict(_DEFAULT_WEIGHTS)


def choose_scenario_name(weights: dict[str, float]) -> str:
    names = list(weights.keys())
    values = list(weights.values())
    return random.choices(names, weights=values, k=1)[0]


def build_scenario(
    name: str,
    total_ticks: int,
    blip_chance: float = NORMAL_BLIP_CHANCE,
    tick_seconds: float = GPS_READ_INTERVAL_S,
) -> Scenario:
    return SCENARIOS[name](total_ticks, blip_chance, tick_seconds)
