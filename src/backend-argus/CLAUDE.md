# CLAUDE.md — src/backend-argus

This file explains why `src/backend-argus` is built the way it is. See `README.md` in this
directory for practical "how do I run this" instructions, and the top-level `CLAUDE.md` for how
this module fits the rest of Argus (the ER model, `cv-argus`, `ui-argus`, the planned ESP32).

## What this is

The Argus cloud backend: a FastAPI service backed by MongoDB (via Beanie), covering exactly six
of the ER diagram's nine entities — **User, Truck, Driver, Route, Status_Route, Alert** — plus
login. `Report`, `Device`, and `Geofence` exist in the ER diagram
(`docs/designs/ER-model.drawio.xml`) but are deliberately **out of scope for this pass**: no
`ui-argus` screen consumes them and no endpoint for them was ever committed in the root
`CLAUDE.md`'s API list, so building CRUD for them now would be unused scaffolding, not a real
deliverable. They're future work, not a spec.

`ui-argus` (the React web frontend) is this backend's only consumer today; its `src/types.ts`
was written to mirror the ER model field-for-field in anticipation of this backend, so most of
the work here is making that anticipation correct rather than negotiating a new contract. The
other real caller is the not-yet-built ESP32 firmware — see "Device auth" below — `cv-argus`
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
name `vigilance` (enum: `normal` / `low_vigilance` / `critical`) instead of reverting to the
diagram's name. This is a deliberate kept rename, not an unnoticed divergence from the diagram.

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

| Role | Users | Trucks/Drivers | Routes | Status_Route | Alerts |
|---|---|---|---|---|---|
| `root_admin` | full CRUD (any role) | full CRUD | full CRUD | read | full CRUD |
| `admin` | scoped: create/view/edit/deactivate **`guardian`-role accounts only** — see below | full CRUD | full CRUD | read | none |
| `guardian` | none | read-only | read-only | read-only | read + review (`PUT`) only |
| `truck_driver` | none | read | read | read | read |

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

`POST /api/alerts` and `POST /api/routes/{id}/status` are the two endpoints a logged-in operator
never calls directly — per both this repo's root `CLAUDE.md` and `cv-argus`'s own `CLAUDE.md`,
the real HTTP caller is ESP32 firmware relaying records off the Pi's Bluetooth buffer. Requiring
a full username/password login-plus-refresh flow on an embedded device is unneeded complexity:
no secure token storage story on bare-metal, no session to refresh.

Instead, `Truck.device_api_key_hash` holds a bcrypt-hashed, per-truck static key:

- Generated once via `POST /api/trucks/{id}/rotate-key` (root_admin only), returned in plaintext
  **exactly once**, then provisioned into the ESP32's firmware config out of band —
  GitHub-personal-access-token style, not stored anywhere retrievable afterward.
- Ingestion requests carry it as `X-Device-Api-Key`. `authorize_device_or_user()`
  (`app/auth/dependencies.py`) checks it specifically against the truck that owns the target
  route (resolved server-side from the route/alert body, not trusted from the header) — so a
  compromised key can only write that one truck's data, not an arbitrary truck's.
- Both endpoints also accept a `root_admin`/`guardian` JWT as a fallback (manual testing/demo
  seeding without real hardware, e.g. `scripts/seed_dev_data.py`), via the same
  `authorize_device_or_user()` call trying the device key first and the JWT second.

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

## Coordination note — cv-argus's actual Alert envelope

A peer session built `cv-argus`'s `alerts/` module in parallel with this backend. Its confirmed
envelope: `{id, kind: "drowsiness"|"route_status", level, created_at_ms, source_id, payload,
geolocation}`. Two things from that exchange shaped this backend's schema directly:

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

**Since this section was first written**: the hermetic suite has grown to 48 tests (the `admin`
role's scoped RBAC, self-service `/api/auth/me`, `SEED_DEMO_DATA` seeding, and
`SEED_CREDENTIALS_FILE` all added their own coverage — see "RBAC — four roles" and "Startup demo
seeding" above) — the "32 tests"/"27 tests"/"46 tests" figures elsewhere on this page are the
count *at the time each of those sections was written*, not stale claims to reconcile against
each other. `ui-argus` is well past its "first
toolchain run" too — every screen now calls this backend for real, not just field-name-verified
against it; see that module's own `CLAUDE.md` for its current status.

What genuinely hasn't been exercised: real concurrent load, MongoDB running as anything other
than a single local container (no replica set, no auth), and — since `ui-argus` doesn't call
any real API yet (`INTEGRATION.md`'s cross-cutting gaps are all still open) — an actual browser
session driving this backend through the UI rather than `curl`. Treat those as the honest next
steps, not something to describe as production-validated without having actually done them.

## Future work (explicitly out of scope for this pass)

- `Report`, `Device`, `Geofence` — modeled in the ER diagram, no consumer yet.
- Refresh tokens / session revocation.
- Real per-user "own truck/route only" scoping for the `truck_driver` role — needs a schema
  link this pass doesn't add (see "Known RBAC gap" above).
- A real-time push strategy (WebSocket/SSE) for the live dashboard, instead of polling
  `GET /api/routes/active` — `ui-argus/INTEGRATION.md` already tracks this as an open decision;
  it isn't decided here either.
- Verifying `docker-compose.pi.yml`-equivalent concerns don't apply here (this module has no
  hardware dependency, unlike `cv-argus`) — noted only for completeness, not an actual gap.
