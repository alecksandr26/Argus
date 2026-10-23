"""Optional demo disruptions, one of each at most per route, both off by default:

- **Network outage** (`SIMULATOR_OUTAGE_CHANCE`): for 70-150 s every report attempt fails with
  `no_network` without reaching the backend, as when the truck loses mobile data. The ESP32
  rules then play out for real: the points of those reports are dropped (a gap in the path), a
  medium/OK is given up after its 30 s window, and a critical goes to the dead-letter queue (DLQ)
  after its 60 s window. A `no_network` failure is instant and a DLQ replay rides every 5 s
  report, so a dead-lettered critical uses its 4 remaining attempts in about 20 s. It is
  replayed (`delivery: dlq`, "Recovered after lost connection") only if the network comes back
  within those ~20 s, i.e. within about 80 s of the critical being raised; otherwise it is
  archived (`archived:max_attempts`), as on the real device. With a random window that is the
  exception, not the rule: only a critical raised in the last ~80 s of the outage is replayed.
- **Outage around a critical** (`SIMULATOR_OUTAGE_AROUND_CRITICAL`, needs the outage chance too):
  for a dependable replay demo, the outage is placed around the scenario's first critical
  instead, from 5 s before it to 70 s after it. The critical's live window runs out inside the
  outage, and the network is back for its second DLQ attempt, well inside the ~80 s budget.
  `normal` has no critical, so its outage stays random.
- **GPS loss** (`SIMULATOR_GPS_LOSS_CHANCE`): for 30-90 s there is no fix. Points repeat the last
  good position as `last_known` (speed 0, `location_age_s` growing), and an event in that window
  carries that last-known position and `speed: null`, as the ESP32 sends them (contracts C1.8,
  C2.1).

Each disruption is one window `[start, end)` in GPS ticks, at a uniformly random time inside the
route (or around the first critical), or none.
"""
from __future__ import annotations

import random
from typing import Optional

OUTAGE_SECONDS = (70.0, 150.0)  # long enough for a critical to reach the DLQ (60 s window)
GPS_LOSS_SECONDS = (30.0, 90.0)

# Outage around a critical: from this long before it to this long after it. The end has to be
# past the 60 s live window (so the critical is dead-lettered) and under the ~80 s replay budget
# (60 s live + 4 more DLQ attempts, one per 5 s report).
OUTAGE_AROUND_CRITICAL_SECONDS = (5.0, 70.0)


def plan_window(
    chance: float,
    total_ticks: int,
    seconds: tuple[float, float],
    tick_seconds: float = 1.0,
) -> Optional[tuple[int, int]]:
    """`(start, end)` ticks of the route's one window, with probability `chance`, else `None`.
    The window starts after tick 0 and ends before the route does."""
    chance = min(1.0, max(0.0, chance))
    if chance <= 0.0 or random.random() >= chance:
        return None
    total_ticks = max(total_ticks, 3)
    tick_seconds = tick_seconds if tick_seconds > 0 else 1.0
    length = max(1, round(random.uniform(*seconds) / tick_seconds))
    length = min(length, total_ticks - 2)
    start = random.randint(1, max(1, total_ticks - length - 1))
    return start, start + length


def in_window(window: Optional[tuple[int, int]], tick: int) -> bool:
    return window is not None and window[0] <= tick < window[1]


def plan_window_around(
    chance: float,
    anchor_tick: Optional[int],
    total_ticks: int,
    seconds: tuple[float, float] = OUTAGE_AROUND_CRITICAL_SECONDS,
    tick_seconds: float = 1.0,
) -> Optional[tuple[int, int]]:
    """`(start, end)` ticks from `seconds[0]` before `anchor_tick` to `seconds[1]` after it, with
    probability `chance`, else `None` (also `None` with no anchor). Clipped to the route like
    `plan_window`: it starts at tick 1 at the earliest and ends before the route does."""
    chance = min(1.0, max(0.0, chance))
    if anchor_tick is None or chance <= 0.0 or random.random() >= chance:
        return None
    total_ticks = max(total_ticks, 3)
    tick_seconds = tick_seconds if tick_seconds > 0 else 1.0
    before, after = (max(0, round(s / tick_seconds)) for s in seconds)
    start = min(max(1, anchor_tick - before), total_ticks - 2)
    end = max(start + 1, min(anchor_tick + after, total_ticks - 1))
    return start, end
