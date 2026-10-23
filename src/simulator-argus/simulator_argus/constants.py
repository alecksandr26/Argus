"""Device-side constants the simulator mirrors from the frozen final-tweaks contracts
(`docs/designs/final-tweaks-contracts.md`, C2 + C4).

The simulator posts the same combined report a real ESP32 posts (`POST /api/devices/me/report`),
so its cadence, batch size, retry windows and dead-letter rules are copies of the ESP32's
`config.h` values, not tuning knobs of its own. Change them here only when the contract changes.
"""
from __future__ import annotations

# C4 `SIMULATOR_REPORT_INTERVAL_S` (mirrors `REPORT_INTERVAL_MS` = 5000): a points-only report
# goes this long after the previous report ended. Overridable per run through settings
# (`SIMULATOR_REPORT_INTERVAL_S`); this is the default.
SIMULATOR_REPORT_INTERVAL_S = 5.0

# `GPS_READ_INTERVAL_MS` = 1000: one GPS point per second, which is also the simulator's tick.
GPS_READ_INTERVAL_S = 1.0

# `MIN_SEND_GAP_MS` = 1000: a pending live event is retried at most once per second.
MIN_SEND_GAP_S = 1.0

# `ACTIVE_ROUTE_RETRY_MS` = 30000: points-only cadence while the backend says the truck has no
# active route (events still go immediately).
ACTIVE_ROUTE_RETRY_S = 30.0

# `REPORT_MAX_POINTS` = 10 (must match backend-argus `app/constants.py`): the newest 10 points.
REPORT_MAX_POINTS = 10

# Live retry windows (`LIVE_RETRY_CRITICAL_MS` / `_MEDIUM_MS` / `_OK_MS`). Keyed by
# `DeviceEvent.priority_key`: a critical then goes to the dead-letter queue, a medium/OK is
# dropped (`retry_window`).
LIVE_RETRY_WINDOW_S: dict[str, float] = {"critical": 60.0, "medium": 30.0, "ok": 30.0}

# `BUFFER_DLQ_MAX_ATTEMPTS` = 5: a dead-lettered critical is archived after 5 failed attempts
# (the live failure that dead-letters it is attempt 1).
DLQ_MAX_ATTEMPTS = 5

# C2.1 `age_s` upper bound for points (7 days); older points would be skipped by the backend.
MAX_POINT_AGE_S = 604_800.0

# The model name the simulator writes into `event.ai_metadata.model`, so a simulated alert is
# never mistaken for one the real Pi pipeline (`parts-rules-v1`) produced.
SIMULATOR_MODEL_NAME = "simulator"
