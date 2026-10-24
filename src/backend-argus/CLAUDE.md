# CLAUDE.md — src/backend-argus

This file explains why `src/backend-argus` is built the way it is. See `README.md` in this
directory for practical "how do I run this" instructions, and the top-level `CLAUDE.md` for how
this module fits the rest of Argus (the ER model, `cv-argus`, `ui-argus`, the planned ESP32).

## What this is

The Argus cloud backend: a FastAPI service backed by MongoDB (via Beanie), covering exactly six
of the ER diagram's nine entities — **User, Truck, Driver, Route, Status_Route, Alert** — plus
login, and three collections that aren't ER entities: **`incidents`** (groups of alerts a
guardian closes), **`driver_oks`** (the driver's "I'm OK" per episode) and `simulator_sessions`
(cost guardrails). Since the final-tweaks work (`docs/designs/final-tweaks-plan.md`, wire
details frozen in `docs/designs/final-tweaks-contracts.md`, which wins over this file on any
detail) the device writes through one endpoint, `POST /api/devices/me/report`, and the
dashboard reads through one poll, `GET /api/live` — see "The final-tweaks data model" below. `Report`, `Device`, and `Geofence` exist in the ER diagram
(`docs/designs/ER-model.drawio.xml`) but are deliberately **out of scope for this pass**: no
`ui-argus` screen consumes them and no endpoint for them was ever committed in the root
`CLAUDE.md`'s API list, so building CRUD for them now would be unused scaffolding, not a real
deliverable. They're future work, not a spec.

`ui-argus` (the React web frontend) is this backend's only consumer today; its `src/types.ts`
was written to mirror the ER model field-for-field in anticipation of this backend, so most of
the work here is making that anticipation correct rather than negotiating a new contract. The
other real callers are the ESP32 firmware (`src/esp32-argus`) and the fleet simulator
(`src/simulator-argus`), both through the device key — see "Device auth" below. `cv-argus`
itself never calls this backend directly (see "Coordination note" below).

## Field names: ER diagram typos fixed here

The ER diagram (`docs/designs/ER-model.drawio.xml`) has a handful of typos and naming
inconsistencies. This backend is the corrected, authoritative field list going forward — treat
the diagram as historical, pre-fix reference, not the live source of truth:

| Entity | Diagram spelling | Fixed to |
|---|---|---|
| Driver | `blod_type` | `blood_type` |
| Alert | `reviwed_by_operator` | `reviewed_by_operator` |
| Route | `Id_Route` (capitalized) | `id_route` |
| several | mixed `update_at` / `updated_at` | `updated_at` everywhere |
| Status_Route | `operative_status` (per the diagram) | kept as `vigilance` — see next section |

`src/ui-argus/src/types.ts` was patched to match in the same change that added this backend
(the fixture file that also needed matching at the time, `src/data/fixtures.ts`, is deleted now
that every screen fetches real data) — see that module's own `CLAUDE.md`/`INTEGRATION.md` for
status.

## Why `Status_Route.vigilance`, not `operative_status`

The ER diagram names Status_Route's live drowsiness reading `operative_status`, matching the
field name `Truck`, `Driver`, and `Route` each already use for their own, differently-enumerated
status field. Reusing that name for a fourth, unrelated enum would be a real ambiguity — "which
`operative_status`?" — not just an inconsistency, so this backend keeps `ui-argus`'s already-used
name `vigilance` instead of reverting to the diagram's name. This is a deliberate kept rename,
not an unnoticed divergence from the diagram.

As of the severity-taxonomy unification, this field's enum is the same `Severity` type
`Alert.severity_level` uses (`critical`/`medium`/`low`) rather than its own separately-named
3-tier scale — previously `Status_Route.vigilance` was a distinct `Vigilance` enum
(`normal`/`low_vigilance`/`critical`) that didn't even share label names with `Alert`'s
`AlertSeverity`, so status and alerts couldn't be compared on one scale. See
`app/models/common.py`'s `Severity` docstring for the retired `Vigilance` enum's old values and
their mapping onto the new one (`normal -> low`, `low_vigilance -> medium`, `critical ->
critical`).

Since the final-tweaks work the field is its own `Vigilance` enum again, but only as the
`Severity` scale **plus `unknown`**: the ESP32 sends `unknown` when it has had no Pi reading for
10 s (or none since boot), and a legacy `POST /api/routes/{id}/status` without `vigilance` now
stores `unknown` instead of silently defaulting to `low`. `unknown` never counts as "OK" when a
route's `incident_state` is cleared (C3.5).

## Auth design

- **Password hashing**: the `bcrypt` package directly (`hashpw`/`checkpw`), not `passlib`.
  `passlib` is unmaintained (no release since 2020) and its bcrypt backend breaks under
  `bcrypt>=4.1` (it probes for a `__about__` attribute recent bcrypt releases removed) — calling
  bcrypt directly avoids that failure mode for one extra line of code.
- **JWT**: `PyJWT`, `HS256`, a single server secret (`JWT_SECRET`). `python-jose` (what older
  FastAPI tutorials use) was avoided — its upstream is unmaintained with a CVE history; PyJWT is
  the actively-maintained, narrower-scope library FastAPI's current docs point to instead.
- `POST /api/auth/login` takes a plain `{email, password}` JSON body, not
  `OAuth2PasswordRequestForm` — that form hardcodes a `username` field and pulls in OAuth2's
  third-party-client Swagger semantics this app doesn't need (there's one first-party SPA
  client). Protected routes use `HTTPBearer()`, not `OAuth2PasswordBearer`, for the same reason.
- No refresh-token flow in this pass — `JWT_EXPIRE_MINUTES` (default 480, one shift) is the only
  session-length control. Documented future work, not an oversight.
- **Every `password` field on this API's boundary (`LoginRequest`, `UserCreate`, `UserUpdate`)
  carries a SHA-256 hex digest of the real password, not the raw password itself.** `ui-argus`
  computes this client-side via `crypto.subtle.digest` (`src/utils/crypto.ts`'s `sha256Hex`)
  before the request ever leaves the browser; this backend then `hash_secret()`s (bcrypt) *that
  digest* exactly like it would any other secret — `hash_secret`/`verify_secret` themselves
  needed no code changes, only the semantic meaning of "password" changed everywhere it's
  accepted. The contract is enforced, not just documented: `app/schemas/common.py`'s
  `Sha256HexDigest` type (`Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]`) rejects
  a non-conforming payload with `422` before it ever reaches `verify_secret`/`hash_secret`. This
  is a defense-in-depth layer on top of TLS, not a replacement for it — and it has a useful side
  effect: `crypto.subtle` is only available in a secure context (HTTPS or `localhost`) by browser
  spec, so a production deployment served over plain HTTP simply can't compute this and login
  fails outright, a deliberate nudge toward HTTPS rather than a bug to route around.

### Self-service profile — `GET`/`PUT /api/auth/me`

`/api/users/*` is entirely `root_admin`/scoped-`admin`-gated — even `GET` — so there was no way
for a `guardian`/`truck_driver` (or anyone) to view or edit their *own* email/name/phone/
password. These two routes live in `app/routers/auth.py`, not `users.py`, specifically because
`users.py`'s auth check needed to stay a per-route `Depends(require_role(...))` (see "RBAC" above)
and adding a third, role-unrestricted route to that same router would be an easy place to
accidentally weaken that scoping later. Both routes are gated by `Depends(get_current_user)`
only — any authenticated role, no `/api/users` access needed:

- `GET /api/auth/me` returns the caller's own full `UserOut` (safe — it's read-only and it's
  their own data).
- `PUT /api/auth/me` takes a new `MeUpdate` schema (`app/schemas/auth.py`) that has **no**
  `role`/`is_active` fields defined on it at all — not permission-checked, structurally
  impossible to send. A `role`/`is_active` key in the raw JSON body is silently dropped by
  Pydantic rather than erroring, so self-service profile editing can never be used to escalate
  privilege or reactivate a deactivated account; both stay exclusively `root_admin`'s (or, for
  guardians, a scoped `admin`'s) job via `/api/users/{id}`.

### Root admin bootstrap

Before this, there was no way to get the *first* `root_admin` into a real deployment at all:
`POST /api/users` (the only account-creation endpoint) already requires an existing `root_admin`
JWT to call, and `scripts/seed_dev_data.py` is a manual, destructive dev-only script nobody would
run against production. `app/auth/bootstrap.py`'s `ensure_root_admin()` closes that gap — called
from `app.main`'s `lifespan` on every startup, it creates one `root_admin` from the
`ROOT_ADMIN_EMAIL`/`ROOT_ADMIN_PASSWORD`(+name/phone) env vars if no user exists at that email
yet, idempotently (a restart is always a no-op once the account exists, and an existing user at
that email — even a different role, even if the env vars later change — is left completely
untouched, so a deliberately-rotated password is never silently reset just because the container
restarted). It reproduces the exact `sha256(raw)` → `hash_secret()` pipeline a real browser login
produces, computed server-side from the raw env-var password, so logging in afterward with that
raw password actually works. Dev-safe-but-flagged defaults (`admin@argus.dev` / `changeme123`)
deliberately match `scripts/seed_dev_data.py`'s own credentials, so a bare `docker compose up`
and a later seed-script run agree rather than fight over the same account.

### Startup demo seeding — `SEED_DEMO_DATA`

`ensure_root_admin()` above only ever creates one account — enough to get into a fresh
deployment, but not enough to actually demo the fleet screens (Fleet/Drivers/TravelManagement/
LiveOps all show empty tables with no data). `scripts/seed_dev_data.py`'s `seed()` was refactored
to support two modes sharing one dataset definition (a handful of admin/operator and guardian
users, five trucks spanning every `TruckStatus`, five drivers, four routes across every
`RouteStatus`, and a couple of alerts):

- `reset=True` (the manual `python -m scripts.seed_dev_data` default): wipes every collection
  first, then inserts the full set fresh — deterministic, but destructive. Unchanged from before,
  still an explicit, manual action.
- `reset=False`: additive/idempotent — checks each user/truck/driver by its natural unique key
  (email/plate_number/license_number) and skips it if already present; only adds the demo
  routes/status/alerts if `Route` is currently empty (routes have no natural unique key to dedupe
  by otherwise). This is what `app.main`'s `lifespan` calls, right after `ensure_root_admin()`,
  when `settings.seed_demo_data` (`SEED_DEMO_DATA` env var, default `false`) is true — reusing
  `app.state.mongo_client` rather than opening a second connection. Safe to leave the env var on
  permanently in a dev `docker-compose.yml`: restarting the container never re-wipes or
  duplicates anything, it just fills in whatever's missing. Never set this in a real deployment —
  it's a convenience for local/demo environments only.

**Getting the seeded credentials into a file, not just stdout** — `SEED_CREDENTIALS_FILE`: both
`seed()` call sites (the manual script and the `SEED_DEMO_DATA` startup path) already print every
seeded user's role/email/password to stdout, but that's easy to lose in container logs. Setting
`SEED_CREDENTIALS_FILE` to a path (`app/config.py`'s `seed_credentials_file`, empty/disabled by
default) makes `seed()` additionally write the same `role email password` lines to that path via
`scripts/seed_dev_data.py`'s `_write_credentials_file()` — a convenience for a manual QA pass or
for `it-argus`'s Playwright tests to read logins from, not a new secret surface: every password
written is the same fixed, already-public `DEMO_PASSWORD` (`changeme123`) already printed above
and documented in README.md. In Docker, the path needs to land under the `./app:/app/app` bind
mount (e.g. `/app/app/seed_credentials.txt`) to actually show up on the host — see both
`docker-compose.yml` files' comments. The output file is gitignored (`seed_credentials*.txt`);
never commit one. Since it's a plain `${VAR:-default}`-style env var in both `docker-compose.yml`
files, it (and `SEED_DEMO_DATA`) can be set directly on the `docker compose up --build` command
line instead of in `.env` — e.g.
`SEED_DEMO_DATA=true SEED_CREDENTIALS_FILE=/app/app/seed_credentials.txt docker compose up
--build` — see README.md's "Seeding demo data" for the exact commands from both this module's own
compose file and the repo-root one.

### RBAC — four roles

`Role` (`root_admin` / `admin` / `guardian` / `truck_driver`). The first three originally read
directly off the root `CLAUDE.md`'s "Three actor roles" bullet; `admin` was added later as a
deliberate product decision (an operations role — schedules routes, manages the truck/driver
roster — distinct from `root_admin`'s full control and `guardian`'s read-only monitoring), not
something any design doc anticipated in advance.

| Role | Users | Trucks/Drivers | Routes | Status_Route | Alerts | Incidents |
|---|---|---|---|---|---|---|
| `root_admin` | full CRUD (any role) | full CRUD | full CRUD + **stop route** | read | read + review + de-escalate + delete | read + **close** |
| `admin` | scoped: create/view/edit/deactivate **`guardian`-role accounts only** — see below | full CRUD | full CRUD + **stop route** | read | read | read |
| `guardian` | none | read-only | read-only | read-only | read + review (`PUT`, notes) + de-escalate | read + **close** |
| `truck_driver` | none | read | read | read | read | read |

"Read" for Routes/Status_Route/Alerts/Incidents includes `GET /api/live` and
`GET /api/routes/{id}/track` (any authenticated user; a device key gets 401). Closing an incident
(`POST /api/incidents/{id}/close`) is guardian + root_admin, `admin`/`truck_driver` 403. Stopping a
route (`POST /api/routes/{id}/stop`) is root_admin + admin, guardian/truck_driver 403. Writing
alerts and points is the device's job (device key), not a role's; the legacy JWT fallback on the
old ingestion endpoints is root_admin/guardian (see "Device auth").

Plus one resource outside the ER model: **`/api/simulator`** (cloud simulator on/off) is
**`root_admin` only** for every method; every other role gets 403. See "Simulator control" below.

**`admin`'s user-management scope is narrow and enforced server-side, not just by convention**:
`app/routers/users.py` moved its auth check from a router-level `dependencies=[...]` (whose
return value isn't injectable) to a per-route `actor: User = Depends(require_role(Role.ROOT_ADMIN,
Role.ADMIN))` parameter, so every handler can reason about *whose* request this is:

- `list_users`/`get_user`/`update_user`/`delete_user`: an `admin` actor touching a
  non-`guardian` target (including another `admin`, or `root_admin` itself) gets **404**, not
  403 — this is deliberate: an `admin` shouldn't even be able to detect that another admin/
  root_admin account exists by probing ids, not just be blocked from editing it.
- `create_user`/`update_user`: an `admin` actor supplying a non-`guardian` `role` (on create, or
  trying to change an existing guardian's role away from `guardian` on update) gets **403** —
  can't use this endpoint to create or promote an account into `admin`/`root_admin`.
- `root_admin` is completely unrestricted on all of the above, always.

**Known RBAC gap, not hidden**: the plan called for `truck_driver` to see only *their own*
truck/route/alerts, but the ER diagram has no `User` → `Driver`/`Truck` link to scope by — a
`truck_driver`-role `User` row and a `Driver`/`Truck` row are structurally unrelated right now.
Rather than fake a scoping rule that doesn't have real data behind it, this pass gives
`truck_driver` unscoped read access to the same six resources (see `list_trucks`/`list_drivers`
in `app/routers/`, which both carry a comment pointing back here). Adding the missing link (most
likely a `Driver.id_user` field) and the resulting per-user filtering is real future work, not
implemented now.

### Device (ESP32) auth — deliberately separate from user JWT

A logged-in operator never writes alerts or GPS points: the real HTTP caller is the ESP32
relaying the Pi's records off its Bluetooth link (and the simulator playing one). Requiring
a full username/password login-plus-refresh flow on an embedded device is unneeded complexity:
no secure token storage story on bare-metal, no session to refresh.

Instead, `Truck.device_api_key_hash` holds a bcrypt-hashed, per-truck static key:

- Generated via `POST /api/trucks/{id}/rotate-key` (root_admin **or admin** — admin owns the
  truck roster), returned in plaintext **exactly once** — GitHub-personal-access-token style,
  not stored anywhere retrievable afterward. `ui-argus`'s Fleet screen ("Generate device
  credentials") turns it into an `argus-device.env` for the truck's Pi; `cv-argus` serves it to
  the ESP32 over Bluetooth (`CONFIG`), so nothing per-truck is flashed into firmware. See
  `src/esp32-argus/README.md` section 1b for the full chain.
- `DELETE /api/trucks/{id}/device-key` (root_admin/admin) revokes it. `TruckOut` never carries
  the key or hash, only `has_device_key`, `device_key_issued_at`, and `device_last_seen_at`.
  A successful key match refreshes `device_last_seen_at` at most once a minute
  (`DEVICE_LAST_SEEN_RESOLUTION`, a targeted `$set`, not `truck.save()`); rotating or
  revoking resets it.
- Sent as `X-Device-Api-Key`. The truck is resolved from the key alone (indexed
  `device_api_key_fingerprint`, a SHA-256 of the key, then one bcrypt check), so the device
  needs no route id: `POST /api/devices/me/report` and `GET /api/devices/me/active-route` both
  work that way.
- **Auth cache** (`app/auth/dependencies.py`, `_device_cache`): bcrypt is deliberately slow and
  the device reports every 5 s, so a warm container remembers fingerprint -> truck id for
  `DEVICE_AUTH_CACHE_TTL` (60 s) and skips bcrypt on a hit. An entry is only added after a full
  bcrypt check of that exact key, and a hit still requires the truck's **current** fingerprint
  (the truck document is read on every request anyway) to equal the key's, so a rotate or revoke
  takes effect at once on every container, not after the TTL; the container that handled it also
  drops its entries (`invalidate_device_cache`). SHA-256 equality of a 32-byte random key is
  proof of possession; bcrypt's job is guarding a leaked database, which a cache hit doesn't
  weaken.
- **`POST /api/devices/me/report` is device-key only** (no JWT fallback): it's the one device
  write of the new model, see "The final-tweaks data model" below.
- On the **legacy** ingestion endpoints (see "Legacy device endpoints" below)
  `authorize_device_or_user()` checks the key specifically against the truck that owns the
  target route (resolved server-side from the route/alert, not trusted from the header), so a
  compromised key can only write that one truck's data. `POST /api/alerts` and
  `POST /api/routes/{id}/status` also accept a `root_admin`/`guardian` JWT as a fallback (manual
  testing without hardware), the device key tried first and the JWT second.
- `PUT /api/alerts/{id}` with a device key is scoped to `resolved_at` only (stored as
  `driver_ok_at`): a device-key request that also tries to set `reviewed_by_operator`/
  `operator_notes` gets a `403`, not silently ignored.

## The final-tweaks data model

Contracts C2/C3 in `docs/designs/final-tweaks-contracts.md` are the exact spec (field tables,
every status code); this section says what exists, where it lives, and why. Constants are in
`app/constants.py` (C4).

### `POST /api/devices/me/report` — the one device write (C2)

`app/routers/devices.py` + `app/report.py`. One request carries up to `REPORT_MAX_POINTS` (10)
GPS points and at most one event (`kind: alert`, medium/critical, or `kind: ok`, the driver's
"I'm OK"). A body over 16 KiB is 413, a bad envelope 422; otherwise it's **always 200 with a
result per part**, so a rejected event never costs the device its points, nor the reverse:
`points: {stored, route, status: ok|no_active_route|route_finished}`,
`event: {status: created|duplicate|rejected, reason, id_alert}`, plus `active_route` (the
truck's current route, which is why the ESP32 no longer needs `GET /api/devices/me/active-route`).

- **Times are relative on the wire** (`age_s`): the device has no trustworthy clock, so the
  backend takes `received_at` once per request and computes point `timestamp` and event
  `occurred_at` as `received_at - age_s`, both at millisecond precision (C2.1 [sync 3]). Report
  alerts get `time_source: relative`.
- Points go to the truck's **current** active route (in progress; the newest
  `actual_departure` wins). Each point is validated on its own; an invalid one is skipped, never
  a 422.
- The event goes to the route the truck was on **when it happened** (`actual_departure <=
  occurred_at <= actual_arrival`), so a dead-letter replay is never stamped onto a route that
  started after it. Rejections: `route_finished` (that route has ended, or the event came after
  the truck's last route ended), `no_route` (no route had started yet), `too_old` (over 7 days),
  `wrong_truck` (the id is already used by another truck's event), `invalid`.
- `delivery: dlq` + `queued_seconds` mark an event replayed from the Pi's dead-letter queue —
  the "missing alerts" of the root `CLAUDE.md`. They are stored and grouped like live ones (the
  incident gets `has_replayed`), and the UI labels them "Recovered after lost connection". A
  late event can't move the route's state backwards: alerts only raise it to the max (below).
- An escalation (the same `(id_route, episode)` already has a medium alert) gets
  `related_alert_id` pointing at it.

### Incidents (C3.4) — `app/incidents.py`, `app/routers/incidents.py`

Every medium/critical alert joins its route's **open** incident when it happened less than
`INCIDENT_GROUP_WINDOW` (15 min) before that incident's first alert or after its last one
(several match -> the newest); otherwise it opens a new one. `low` alerts get no incident. An
incident's aggregates (`severity` = max of its alerts, never below medium; first/last alert
time; `alert_count`; `driver_ok_at`; `has_replayed`) are recomputed from its alerts on every
change, a de-escalation included. The dashboard tier is computed at response time: `old` once the
last alert is more than `OLD_INCIDENT_AFTER` (1 h) ago, else the severity.

`GET /api/incidents?route_id=&status=open|closed|all&limit=`, `GET /api/incidents/{id}`, and
`POST /api/incidents/{id}/close` with `{note}`, **guardian and root_admin only**. A note is
required to close a critical incident (422 without one) and optional for a medium one; 409 if
already closed. Closing stamps `closed_at` and `reviewed_by_operator` on every alert of the
incident and may lower the route state. A closed incident never gets new alerts. Migration
closures store `closed_by: null` ("closed by the system") plus an explanatory note; otherwise
`closed_by` is a real user id.

Why incidents and not per-alert resolution: a drowsy episode is a burst of alerts, and a guardian
acts on the episode, not on each alert. The old per-alert `resolved_at` mixed "the driver says
OK" with "a guardian dealt with it"; those are now separate (`driver_ok_at` vs. closing).

### Route `incident_state` (C3.5)

`Route.incident_state` (`fine`/`medium`/`critical`) drives the map marker colour, alongside
`last_not_ok_at`. Only alerts raise it (to the max of its current value and the alert's
severity); points never do. It drops back to `fine` after `ROUTE_STATE_CLEAR_AFTER` (1 h) of
only-`low` points with no gap over `OK_MAX_GAP` (60 s) — silence or `unknown` points clear
nothing — or when closing incidents leaves no open incident at that severity (closing can only
lower it). Internal fields `ok_since`/`last_ok_point_at` track the OK run. Route fields are
written with targeted `$set`s, never a whole-document save, so a concurrent
`PUT /api/routes/{id}` can't undo them.

### `driver_ok_at` — the driver's OK, stored by episode

A `kind: ok` event stores a `DriverOk` (`driver_oks`) and sets `driver_ok_at` on every alert of
that `(id_route, episode)` that doesn't have one yet; an alert of that episode arriving later
gets it too. It never closes anything and never changes `incident_state`: the guardian still
decides. `driver_ok_at` replaces the retired `Alert.resolved_at` (gone from `AlertOut`).

### Route lock (C2.3)

Grouping and route-state work must not interleave across requests (possibly in two Lambda
containers), so every write that touches them takes a per-route lock: an atomic
`findOneAndUpdate` on the Route document with a lease (`route_lock`). A request waits at most
`ROUTE_LOCK_WAIT` (5 s, **per request**: a report's two acquisitions share one deadline) and then
answers **503 "Route is busy, try again"**; a crashed holder frees it after `ROUTE_LOCK_LEASE`
(10 s). The route's status is re-read under the lock, so a stop that lands mid-request gives
`route_finished` instead of a write onto a finished route. Both values are coupled to the ESP32's
8 s attempt timeout and the 10 s Lambda timeout; `src/infra-argus`'s tests read
`app/constants.py` and fail synth if the Lambda side drifts.

### Stopping and finishing routes (C3.1, C3.6)

`POST /api/routes/{id}/stop` with `{reason}` (required, 1..500 chars), **root_admin and admin**.
From `scheduled`/`in_progress`/`in_progress_alert` it sets `cancelled`, `stop_reason`,
`stopped_by`, `stopped_at`, and `actual_arrival` if the route had departed; a finished route is
409. Open incidents stay open. From then on the device gets `route_finished` for points and
events. `PUT /api/routes/{id}` sets `actual_departure`/`actual_arrival` on start/complete when
they're empty (the event-to-route match relies on them), can't reopen a finished route (409), and
takes the route lock when it changes the status, as stop does. `DELETE /api/routes/{id}` also
deletes that route's incidents and driver-OKs.

### `GET /api/live` and `GET /api/routes/{id}/track` (C3.6)

`app/routers/live.py`: everything the dashboard polls every 5 s, in one request — the active
routes (each with its newest position, plate and driver name), **every open incident** (an ended
route's included, flagged `route_ended`), and the GPS track. Without `since` the track is the last
`LIVE_TRACK_INITIAL_WINDOW` (15 min); with `since` (the previous `server_time`) it's the points
received after `since - LIVE_SINCE_OVERLAP` (a 10 s overlap, so a point committed just after the
previous query isn't lost; the UI de-duplicates by `(id_route, t)`). `?route=<id>` narrows all
three to one route, any status. One poll instead of one fetch per route keeps the Lambda
invocation count (the cost driver, `src/infra-argus`'s R8) proportional to open tabs, not to
trucks.

`GET /api/routes/{id}/track?max_points=` (`app/track.py`): a route's whole path, any status,
downsampled above `max_points` (default 2000, max 10000), keeping the first and last points and
the non-`low` ones first. `GET /api/routes/active` is still served (with the new route fields),
but the UI has moved to `/api/live`.

### Dedup indexes and GPS retention (C2.2, C3.3) — `app/indexes.py`

- `alerts`: unique partial `(id_route, client_event_id)` where the id is a string;
  `driver_oks`: unique `(id_route, client_event_id)`; `status_routes`: unique
  `(id_route, timestamp)`. Concurrent duplicates are settled by the index (insert, catch the
  duplicate-key error, answer `duplicate`); duplicate points are skipped.
- **Retention**: the first stored point of each `(id_route, UTC minute)` gets `keep: true`; a TTL
  index on `timestamp` with `partialFilterExpression {keep: false}` expires the rest after
  `GPS_FULL_RETENTION` (7 days). A week of full 1 Hz history, then one point per route per
  minute, which keeps the GPS collection small on a free-tier database.
- These live outside Beanie's `Settings.indexes` because they can conflict with data already in
  a database (duplicates from before the unique index, or the old plain index on the same keys),
  and Beanie raises on a conflict, which would stop the backend from booting. `init_db` calls
  `ensure_indexes(strict=False)` (a warning per failure; the application-level dedup checks
  remain), and the migration script calls it with `strict=True`.

### Migration script — `scripts/migrate_final_tweaks.py`

One-off and idempotent, run once against an existing database after deploying this backend
(`--dry-run` only counts). It moves `resolved_at` -> `driver_ok_at`, backfills
`actual_departure`/`actual_arrival` on routes that predate them, builds incidents (and the
implied `incident_state`) for historical medium/critical alerts, closes the incidents whose alerts
were all reviewed already, sets `keep` and removes duplicate points, clears duplicate alert
`client_event_id`s, then drops the old plain index and creates the unique/TTL ones strictly.
`--close-ended-routes` (off by default; decide before the first real run, a later run has nothing
left to group) also closes the incidents it creates on routes that already ended, so Live
operations doesn't start full of old history. Setting `keep` is destructive once the TTL index
exists: GPS history older than a week is thinned to one point per route-minute. The script's
docstring lists every step.

### Legacy device endpoints, kept for now (C2.4)

`POST /api/alerts`, `POST /api/routes/{id}/status`, `PUT /api/alerts/{id}` (`resolved_at`, device
key) and `POST /api/alerts/{id}/resolve` keep working for old firmware and the old simulator until
every module has merged the new report; they're deleted in a later cleanup, not in this work.
Their behaviour follows the new model:
- `source: panic_button` on create is **422** (the panic button is retired; old `panic_button`
  rows stay readable);
- a created medium/critical alert is grouped into an incident and raises the route state like a
  report event; a `low` one gets `id_incident: null` and touches nothing;
- a status post without `vigilance` stores `unknown`, and its points take the same route-state
  path as report points;
- a device "resolve" (either path) sets `driver_ok_at` and is **409 on a finished route**; a user
  (JWT) `resolved_at`, or a JWT `POST .../resolve`, is **422** ("resolved_at is retired; close
  the incident with POST /api/incidents/{id}/close"). A guardian/root_admin `PUT` review (notes
  only) on a finished route stays allowed;
- `GET /api/devices/me/active-route` is unchanged.

## Simulator control — `/api/simulator` (root_admin only, cost-guarded)

On the AWS demo deployment (`src/infra-argus`), the fleet simulator isn't always running. It's
one ECS Fargate Spot task per session, which root_admin turns on and off from `ui-argus`'s Live
operations screen. `app/simulator_control.py` (`SimulatorController`, boto3 ECS via
`asyncio.to_thread`) + `app/routers/simulator.py`: `GET /api/simulator`, `POST .../start`
(body: only `truck_count` 1–10; `extra="forbid"`, so sending a duration is a 422),
`POST .../stop`.

The backend's share of the hard cost guardrails (full R1–R10 table in
`src/infra-argus/README.md`):
- **R1** every session is exactly `SIMULATOR_SESSION_MINUTES` (10). The task gets that value
  as an env override, and `status()` itself stops a task still alive past `auto_off_at`.
- **R2** one at a time: 409 if Mongo has an open session *or* ECS lists any `argus-simulator`
  task, so an orphaned task still blocks.
- **R3** monthly quota (`SIMULATOR_MONTHLY_QUOTA_MINUTES`, 120 = 12 sessions, UTC calendar
  month), charged the full session up front, never refunded on an early stop; 429 when it's used up.

`SimulatorSession` (new Beanie document, `simulator_sessions`) exists only for those checks. It
isn't an ER-diagram entity. `SIMULATOR_CONTROL=disabled` (the default) makes every endpoint
503 *after* the role check (other roles still see 403), so local dev and the existing test tier
need no AWS at all. Tests swap ECS for an in-memory fake via
`app.dependency_overrides[get_simulator_controller]` (`tests/test_simulator.py`). `boto3` is
imported lazily, so a disabled deployment never needs AWS credentials.

## Geo design

`Route.destination_coordinates`, `Status_Route.current_coordinates`, `Alert.coordinates` are
stored as native MongoDB GeoJSON (`{type: "Point", coordinates: [lon, lat]}`, `2dsphere`-indexed)
so real geo queries are possible — but the API always speaks `{lat, lon}`, exactly
`ui-argus/src/types.ts`'s `Coordinates` shape, so that module's existing `normalizeCoordinates()`
adapter (`src/utils/geo.ts`) needs **zero** changes; it already passes `{lat, lon}` straight
through. `app/geo.py`'s `to_geojson`/`to_latlon` are the only two functions in the codebase that
touch GeoJSON's lon-first ordering — everything else (request schemas, response schemas, the
seed script) works in `{lat, lon}` and calls one of those two at the boundary.

## `GET /api/routes/active` — a dedicated endpoint, not an overloaded status filter

`ui-argus/INTEGRATION.md` flags a real gap: no endpoint lists all currently-active
routes/trucks at once for the live dashboard (`LiveOps.tsx`), only per-route status. The fix
here is `GET /api/routes/active`, not `GET /api/routes?status=active` — `RouteStatus` has no
`"active"` value (it's `in_progress`/`in_progress_alert`/`scheduled`/`completed`/`cancelled`), so
that filter would be a lie about the schema. The dedicated endpoint also returns a genuinely
different shape: each route embeds its **newest** `Status_Route` snapshot plus a truck plate
number and driver name, so the frontend can render markers without a follow-up fetch per route.
Implementation is one query per route for the latest status (`app/routers/routes.py`'s
`list_active_routes`) — fine at this project's fleet sizes (dozens of trucks, not thousands); a
`$lookup` aggregation pipeline is the documented upgrade path if that ever stops being true,
not something to build pre-emptively for a scale this project doesn't have yet.

Still served, now with the new route fields (`incident_state`, `last_not_ok_at`, stop fields),
but `ui-argus` has moved to `GET /api/live`, which returns the same per-route shape plus the open
incidents and the GPS track in one poll (see "The final-tweaks data model").

## Coordination note — cv-argus's actual Alert envelope

A peer session built `cv-argus`'s `alerts/` module in parallel with this backend. Its confirmed
envelope: `{id, kind: "drowsiness"|"route_status", level, created_at_ms, source_id, payload,
geolocation}`. Two things from that exchange shaped this backend's schema directly. (This is
the pre-final-tweaks history: the ESP32 now sends everything through
`POST /api/devices/me/report`, and alert `coordinates` are nullable, `position_unknown` when the
device had no fix. The two endpoints below survive as legacy ones, C2.4.)

- `kind="drowsiness"` maps onto `POST /api/alerts`, `kind="route_status"` onto
  `POST /api/routes/{id}/status` — the ESP32/`sender/` module demuxes by `kind` into these two
  endpoints, confirming the endpoint split above needed no changes.
- `geolocation` is **always `None` coming out of cv-argus** — the Pi has no GPS in this design;
  the ESP32 attaches its own live GPS reading when it relays a record over HTTP. So
  `AlertCreate`/`StatusRouteCreate` require `coordinates`/`current_coordinates` on the *request*
  — this backend expects the ESP32 to have already filled them in, not cv-argus.
- cv-argus's payload carries no model-version string and no clip duration
  (`FusedDrowsinessDetector` runs a rolling 100-frame/20s window, not a discrete clip, and
  doesn't expose a version string per-alert) — hence `AlertAiMetadata.model`/`clip_seconds` are
  **nullable**, not required, in `app/models/alert.py`. Revisit if/when cv-argus starts
  populating them; no reason to block on that now.
- `Alert.source` (`fusion`/`panic_button`) distinguishes alerts derived from cv-argus's
  `kind: "drowsiness"` envelope (via the ESP32's fused decision) from ESP32-native
  `panic_button` events that never touch cv-argus at all. Critically, `cv-argus`'s own envelope
  is now one **input** to the ESP32's fusion decision (combined with its own live grip reading),
  not something relayed 1:1 into a backend `Alert` — see `src/esp32-argus/README.md`'s fusion
  section and `src/cv-argus/src/orchestrator/fusion_contract.py`'s reference decision logic.
  `ai_metadata` and the then-new `grip_status` field were populated (and required) only when
  `source == "fusion"`; both are `null` for `panic_button`, which is unconditionally `critical`
  with no camera/grip evaluation at all. `related_alert_id` links an escalated alert back to the
  incident it escalated from (a new row, not a mutation — `Alert` stays append-only).
  `resolved_at` (recovery, once settable by a guardian or the device) is **retired**: the
  driver's OK is now `driver_ok_at` and a guardian closes the incident instead (see "The
  final-tweaks data model"). Since then `panic_button` is read-only (creating one is a 422), and
  `ai_metadata`/`grip_status` are no longer required for `fusion` alerts: report alerts carry
  `ai_metadata` when the Pi has scores and never `grip_status`.

  **Manual de-escalation** (`POST /api/alerts/{id}/de-escalate`, body `{severity_level,
  reason}`) is the one path that rewrites `severity_level` in place. The ESP32's fusion loop
  never steps a severity down on its own (only a full recovery clears an incident), so a
  guardian/root_admin who has confirmed with the driver that an alert is a false positive or has
  eased can lower it. The target must be strictly lower than the current severity (`422`
  otherwise) and `reason` is required. The first de-escalation copies the device-reported value
  into `original_severity_level`. Each one records `deescalated_by` (user id),
  `deescalated_at`, and `deescalation_reason`, and sets `reviewed_by_operator`. Device keys get
  `401` and `admin` gets `403`, the same roles as reviewing. A de-escalation recomputes the
  alert's incident severity (never below medium).

## Docker build test gate

`Dockerfile` is multi-stage: a `test` stage runs the hermetic pytest tier (27 tests,
`mongomock-motor`, no real Mongo — same `pytest` invocation as local dev) *during* `docker
build`, not as a separate CI step — this project deliberately has no CI (no `.github/`, no
GitHub Actions) by explicit choice, so the build itself is the only gate there is. `runtime` is
`FROM base`, not `FROM
test`, and pulls back only `/app/app` via `COPY --from=test /app/app ./app` — content-wise a
no-op (`test`'s `/app/app` is identical to `base`'s; the stage only additionally copied
`tests/`/`pyproject.toml` alongside it), but it forces Docker to build and pass the `test` stage
before `runtime` can be built at all. That's the actual mechanism that makes a failing test fail
`docker build` outright — a plain multi-stage `FROM` chain without an explicit `COPY --from=`
dependency does **not** guarantee this (verified directly: the classic builder here will happily
skip an unreferenced stage depending on file order and `--target`). `tests/`/`pyproject.toml`
never reach the `runtime` image — confirmed by shelling into a built image and checking
`/app/tests` doesn't exist. Verified both directions for real, not just reasoned through: a
clean build passes all 27 tests then produces a working image; a deliberately-broken test
(`assert False`) failed `docker build` outright with the real pytest output, no image produced.

## Current status

Implemented and **verified end to end against real infrastructure**, not just written — Docker
was available in the session that built this, so every layer below was actually run, not left
as a "should work" claim:

- The hermetic test suite passes (`pytest` from this directory, 32 tests, `mongomock-motor`
  backing Beanie — no real Mongo needed; this count includes the root_admin bootstrap and
  SHA-256-digest contract tests — see "Root admin bootstrap" above). **`docker build`/`docker
  compose build` now run this same suite automatically as a build gate** — see "Docker build
  test gate" above.
- The opt-in `pytest -m mongo` tier (real MongoDB via testcontainers) also passes, confirming
  genuine `2dsphere` behavior, not just the mock's approximation.
- `docker compose up --build` (this module's own compose file) boots a real `backend-argus` +
  `mongo` pair; `GET /health` and `GET /docs` both respond, and `POST /api/auth/login` correctly
  round-trips a real query against the live database.
- `scripts/seed_dev_data.py` ran successfully against that live Mongo container.
- The full root-level `docker compose up --build` (backend + Mongo + `ui-argus` together) booted
  all three services; real HTTP round-trips confirmed against the live stack: login issuing a
  working JWT, `GET /api/routes/active` returning a route with its embedded latest status +
  truck/driver refs, and the device-API-key path correctly rejecting a wrong key (401) and
  accepting the real one (201) for `POST /api/routes/{id}/status`.
- `ui-argus`'s field-name changes (see "ui-argus mechanical diff" below) were verified against
  that same live container: `npx tsc --noEmit` and `npm run build` both passed clean — this was
  `ui-argus`'s first-ever real toolchain run (see that module's own `CLAUDE.md`).

**Since this section was first written**: the hermetic suite has grown to 53 tests (the `admin`
role's scoped RBAC, self-service `/api/auth/me`, `SEED_DEMO_DATA` seeding,
`SEED_CREDENTIALS_FILE`, the severity-taxonomy unification, and the `review_alert` 401-vs-403 fix
all added their own coverage — see "RBAC — four roles" and "Startup demo seeding" above, and
"Coordination note" for the severity work) — the "32 tests"/"27 tests"/"46 tests"/"48 tests"
figures elsewhere on this page are the count *at the time each of those sections was written*,
not stale claims to reconcile against each other. `ui-argus` is well past its "first
toolchain run" too — every screen now calls this backend for real, not just field-name-verified
against it; see that module's own `CLAUDE.md` for its current status.

**Final-tweaks work** (report endpoint, incidents, route state, `/api/live`, track, stop route,
dedup/TTL indexes, auth cache, migration script, legacy endpoints per C2.4): hermetic suite at
**362 passed, 3 xfailed** (2026-10-23, `pytest -q`). Hermetic means `mongomock-motor`: the unique
and partial TTL indexes, and the route lock's `findOneAndUpdate` under real concurrency, are only
approximated there; the migration script has a hermetic test but hasn't run against a real
database yet, and none of this has been deployed to the AWS stack yet.

What genuinely hasn't been exercised: real concurrent load, and MongoDB running as anything other
than a single local container (no replica set, no auth). An actual browser session driving this
backend through the UI (rather than `curl`) is now real too, but only for the login flow —
`src/it-argus`'s Playwright suite covers the root-admin bootstrap login, a wrong-password error,
an unauthenticated deep-link redirect, and sign-out, all against a real `ui-argus` + this backend
+ Mongo. Every other screen (`ui-argus`'s `INTEGRATION.md` full list) calls this backend for real
but has no browser-level test yet — `docs/roadmap.md`'s `it-argus` section tracks that gap.
Treat what's above as the honest state, not something to describe as production-validated beyond
what's actually been run.

## Future work (explicitly out of scope for this pass)

- **"Missing alerts"** (root `CLAUDE.md`, "Pending feature"): the backend side is now built by
  the final-tweaks work — a replayed event carries `delivery: dlq` + `queued_seconds`, is routed
  by the time it happened, never lowers or overwrites newer route state, and marks its incident
  `has_replayed`. What's left there is the root brief's own wording (it predates incidents) and
  any reporting on replays.
- Delete the legacy device endpoints (C2.4) once every module has merged the report endpoint.
- `Report`, `Device`, `Geofence` — modeled in the ER diagram, no consumer yet.
- Refresh tokens / session revocation.
- Real per-user "own truck/route only" scoping for the `truck_driver` role — needs a schema
  link this pass doesn't add (see "Known RBAC gap" above).
- A real-time push strategy (WebSocket/SSE) for the live dashboard, instead of polling
  `GET /api/live` every 5 s — `ui-argus/INTEGRATION.md` already tracks this as an open decision;
  it isn't decided here either.
- Verifying `docker-compose.pi.yml`-equivalent concerns don't apply here (this module has no
  hardware dependency, unlike `cv-argus`) — noted only for completeness, not an actual gap.
