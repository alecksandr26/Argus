"""Named per-virtual-truck behavior profiles.

Vigilance/alerts are scripted per profile, not pure randomness, so a demo run tells a coherent
story instead of an undifferentiated wall of random severities -- but *which* profile a given
truck gets, and exactly when within its run a scripted event lands, is randomized and
parametrized (weighted profile choice, a configurable "quiet blip" probability, and event timing
scaled to the route's own configured duration) rather than hardcoded -- see CLAUDE.md's
"Scenario design" section for the reasoning.
"""
from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class AlertEvent:
    alert_type: str
    severity_level: str
    source: str
    ai_metadata: Optional[dict]
    grip_status: Optional[str]


class Scenario:
    """`normal`: vigilance stays `low` with occasional `medium` blips; no alerts. This is
    typically the most heavily-weighted profile, so the live dashboard isn't wall-to-wall
    incidents.

    A blip's *trigger* is a per-tick coin flip (`medium_blip_probability`), but once triggered
    it *holds* `medium` for a randomized realistic dwell (`_MEDIUM_DWELL_SECONDS_MIN`-`_MAX` of
    wall-clock time, converted to ticks via `interval_seconds`) instead of reverting the very
    next tick -- a fresh independent draw every tick made "medium" last only ~1 tick (~5s at the
    default interval), which read as the alert flickering rather than a real, observable state.
    """

    name = "normal"

    _MEDIUM_DWELL_SECONDS_MIN = 30.0
    _MEDIUM_DWELL_SECONDS_MAX = 120.0

    def __init__(
        self,
        total_ticks: int,
        medium_blip_probability: float = 0.1,
        interval_seconds: float = 5.0,
    ) -> None:
        self._total_ticks = max(total_ticks, 1)
        self._medium_blip_probability = medium_blip_probability
        self._interval_seconds = interval_seconds if interval_seconds and interval_seconds > 0 else 5.0
        self._fired = False
        self._medium_until_tick: Optional[int] = None

    def vigilance(self, tick: int) -> str:
        return self._blip_vigilance(tick, self._medium_blip_probability)

    def _blip_vigilance(self, tick: int, probability: float) -> str:
        if self._medium_until_tick is not None:
            if tick < self._medium_until_tick:
                return "medium"
            self._medium_until_tick = None
        if random.random() < probability:
            dwell_seconds = random.uniform(
                self._MEDIUM_DWELL_SECONDS_MIN, self._MEDIUM_DWELL_SECONDS_MAX
            )
            dwell_ticks = max(1, round(dwell_seconds / self._interval_seconds))
            self._medium_until_tick = tick + dwell_ticks
            return "medium"
        return "low"

    def alert(self, tick: int) -> Optional[AlertEvent]:
        return None

    def should_resolve(self, tick: int) -> bool:
        return False


class DrowsyEscalationScenario(Scenario):
    """Drifts `low -> medium -> critical` over a fraction of the route's own configured
    duration, fires one `fusion` alert at the peak (plausible CNN+LSTM scores and a bad grip
    reading), then recovers back to `low` and auto-resolves that alert -- mirrors the fusion
    contract's debounce/escalation/recovery shape
    (`src/cv-argus/src/orchestrator/fusion_contract.py`) without reimplementing it. Trigger
    points are fractions of `total_ticks`, not fixed tick counts, so the same profile plays out
    coherently whether the route takes 5 minutes or 5 hours."""

    name = "drowsy_escalation"

    _ESCALATE_FRACTION = 0.15
    _PEAK_FRACTION = 0.30
    _RECOVER_FRACTION = 0.55
    _MIN_CYCLE_TICKS = 8

    def __init__(
        self,
        total_ticks: int,
        medium_blip_probability: float = 0.1,
        interval_seconds: float = 5.0,
    ) -> None:
        super().__init__(total_ticks, medium_blip_probability, interval_seconds)
        self._resolved = True
        cycle = max(self._MIN_CYCLE_TICKS, self._total_ticks // 2)
        self._escalate_at = max(1, round(cycle * self._ESCALATE_FRACTION))
        self._peak_at = max(self._escalate_at + 1, round(cycle * self._PEAK_FRACTION))
        self._recover_at = max(self._peak_at + 1, round(cycle * self._RECOVER_FRACTION))
        self._cycle_length = self._recover_at + max(2, round(cycle * 0.2))

    def vigilance(self, tick: int) -> str:
        cycle = tick % self._cycle_length
        if cycle < self._escalate_at:
            return "low"
        if cycle < self._peak_at:
            return "medium"
        if cycle < self._recover_at:
            return "critical"
        return "low"

    def alert(self, tick: int) -> Optional[AlertEvent]:
        cycle = tick % self._cycle_length
        if cycle == self._peak_at and not self._fired:
            self._fired = True
            self._resolved = False
            return AlertEvent(
                alert_type="drowsiness",
                severity_level="critical",
                source="fusion",
                ai_metadata={
                    "model": None,
                    "scores": {"not_drowsy": 0.12, "drowsy": 0.88},
                    "clip_seconds": None,
                },
                grip_status="bad",
            )
        if cycle == self._escalate_at:
            self._fired = False
        return None

    def should_resolve(self, tick: int) -> bool:
        cycle = tick % self._cycle_length
        if cycle == self._recover_at and self._fired and not self._resolved:
            self._resolved = True
            return True
        return False


class PanicScenario(Scenario):
    """Vigilance stays low/medium throughout; a single unresolved `panic_button` alert fires at
    a random tick within the first third of the route and is deliberately left open, matching
    how a real panic event waits for a human to review it in Alert Triage rather than
    auto-recovering."""

    name = "panic"

    def __init__(
        self,
        total_ticks: int,
        medium_blip_probability: float = 0.1,
        interval_seconds: float = 5.0,
    ) -> None:
        super().__init__(total_ticks, medium_blip_probability, interval_seconds)
        window = max(2, self._total_ticks // 3)
        self._fire_at = random.randint(1, window)

    def vigilance(self, tick: int) -> str:
        return self._blip_vigilance(tick, self._medium_blip_probability * 1.5)

    def alert(self, tick: int) -> Optional[AlertEvent]:
        if tick == self._fire_at and not self._fired:
            self._fired = True
            return AlertEvent(
                alert_type="panic_button",
                severity_level="critical",
                source="panic_button",
                ai_metadata=None,
                grip_status=None,
            )
        return None


SCENARIOS: dict[str, type[Scenario]] = {
    "normal": Scenario,
    "drowsy_escalation": DrowsyEscalationScenario,
    "panic": PanicScenario,
}

_DEFAULT_WEIGHTS = {name: 1.0 for name in SCENARIOS}


def parse_scenario_weights(spec: str) -> dict[str, float]:
    """Parses `"normal=0.45,drowsy_escalation=0.35,panic=0.20"` into a weight dict. Unknown
    names and malformed entries are dropped silently (logged by the caller if it cares); if
    nothing valid parses, falls back to an equal split across every known scenario so a
    typo'd `SIMULATOR_SCENARIO_WEIGHTS` never crashes provisioning."""
    weights: dict[str, float] = {}
    for entry in spec.split(","):
        entry = entry.strip()
        if not entry or "=" not in entry:
            continue
        name, _, raw_value = entry.partition("=")
        name = name.strip()
        if name not in SCENARIOS:
            continue
        try:
            value = float(raw_value.strip())
        except ValueError:
            continue
        if value > 0:
            weights[name] = value
    return weights or dict(_DEFAULT_WEIGHTS)


def choose_scenario_name(weights: dict[str, float]) -> str:
    names = list(weights.keys())
    values = list(weights.values())
    return random.choices(names, weights=values, k=1)[0]


def build_scenario(
    name: str,
    total_ticks: int,
    medium_blip_probability: float = 0.1,
    interval_seconds: float = 5.0,
) -> Scenario:
    return SCENARIOS[name](total_ticks, medium_blip_probability, interval_seconds)
