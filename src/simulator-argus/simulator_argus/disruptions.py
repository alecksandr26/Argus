"""Optional demo disruptions, one of each at most per route, both off by default:

- **Network outage** (`SIMULATOR_OUTAGE_CHANCE`): for 70-150 s every report attempt fails with
  `no_network` without reaching the backend, as when the truck loses mobile data. The ESP32
  rules then play out for real: the points of those reports are dropped (a gap in the path), a
  medium/OK is given up after its 30 s window, and a critical goes to the dead-letter queue after
  60 s and is replayed (`delivery: dlq`, "Recovered after lost connection") once the network is
  back.
- **GPS loss** (`SIMULATOR_GPS_LOSS_CHANCE`): for 30-90 s there is no fix. Points repeat the last
  good position as `last_known` (speed 0, `location_age_s` growing), and an event in that window
  carries that last-known position and `speed: null`, as the ESP32 sends them (contracts C1.8,
  C2.1).

Each disruption is one window `[start, end)` in GPS ticks, at a uniformly random time inside the
route, or none.
"""
from __future__ import annotations

import random
from typing import Optional

OUTAGE_SECONDS = (70.0, 150.0)  # long enough for a critical to reach the DLQ (60 s window)
GPS_LOSS_SECONDS = (30.0, 90.0)


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
