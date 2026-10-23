"""Backend constants shared with the other modules (docs/designs/final-tweaks-contracts.md, C4).

Values marked "must match" are mirrored in another module; change both together.
"""
from __future__ import annotations

from datetime import timedelta

# Points per device report (`POST /api/devices/me/report`). Must match the ESP32's
# `REPORT_MAX_POINTS` in `src/esp32-argus/src/esp32_argus/config.h`.
REPORT_MAX_POINTS = 10
# Largest accepted report body, in bytes (larger -> 413).
REPORT_MAX_BODY_BYTES = 16 * 1024

# Incidents (C3.4): a medium/critical alert joins a route's open incident when it happened less
# than this long before the incident's first alert or after its last one.
INCIDENT_GROUP_WINDOW = timedelta(minutes=15)
# An incident whose last alert is older than this is shown in the "old" tier.
OLD_INCIDENT_AFTER = timedelta(hours=1)

# Route `incident_state` (C3.5): it drops back to `fine` after this long of only-OK GPS points...
ROUTE_STATE_CLEAR_AFTER = timedelta(hours=1)
# ...with no gap between two OK points longer than this.
OK_MAX_GAP = timedelta(seconds=60)

# `GET /api/live` without `since`: how far back the track goes. A `since` older than this is
# clamped to it.
LIVE_TRACK_INITIAL_WINDOW = timedelta(minutes=15)
# `GET /api/routes/{id}/track`: default (and the cap of `max_points`).
TRACK_MAX_POINTS = 2000
TRACK_MAX_POINTS_LIMIT = 10000

# GPS point retention: every point is kept this long, then only one point per route per minute
# (`StatusRoute.keep`), via a partial TTL index.
GPS_FULL_RETENTION = timedelta(days=7)

# Warm-container cache of device-key fingerprint -> truck id (app/auth/dependencies.py).
DEVICE_AUTH_CACHE_TTL = timedelta(seconds=60)

CLOSE_NOTE_MAX_LEN = 1000
STOP_REASON_MAX_LEN = 500

# Per-route lock (app/incidents.py `route_lock`, C2.3 [sync 2]). A request waits at most
# ROUTE_LOCK_WAIT for it, then answers 503 (the device counts a failed attempt and retries).
# The wait is per request, not per acquisition (C2.3 [sync 3]): a report that takes the lock
# twice (points, then its event) shares one deadline, received_at + ROUTE_LOCK_WAIT.
# A holder keeps it at most ROUTE_LOCK_LEASE; a crashed (or Lambda-killed) holder frees it then.
#
# Coupled to the deployment, keep these true when changing either side:
#   ROUTE_LOCK_WAIT  <  the ESP32's 8 s attempt timeout  (else the 503 never reaches it)
#   ROUTE_LOCK_WAIT  <  the backend Lambda timeout       (src/infra-argus/lib/app-stack.ts)
#   ROUTE_LOCK_LEASE >= the backend Lambda timeout       (a killed holder's lock outlives it)
# src/infra-argus/test reads this file and asserts the two Lambda inequalities at synth time.
ROUTE_LOCK_LEASE = timedelta(seconds=10)
ROUTE_LOCK_WAIT = timedelta(seconds=5)

# `GET /api/live?since=`: points received up to this long before `since` are sent again, so a
# point whose report was received just before the previous `server_time` but committed after
# that poll's query isn't lost. The UI de-duplicates by `(id_route, t)` (C3.6 [sync 2]).
LIVE_SINCE_OVERLAP = timedelta(seconds=10)
