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

`src/ui-argus/src/types.ts` and `src/ui-argus/src/data/fixtures.ts` were patched to match in the
same change that added this backend — see that module's own `CLAUDE.md`/`INTEGRATION.md` for
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

### RBAC — three roles

`Role` (`root_admin` / `guardian` / `truck_driver`) reads directly off the root `CLAUDE.md`'s
"Three actor roles" bullet, replacing `ui-argus`'s placeholder `'guard' | 'admin'` guess (its
`src/types.ts` doc comment flagged this as unreconciled pending a real backend).

| Role | Users | Trucks/Drivers | Routes | Status_Route | Alerts |
|---|---|---|---|---|---|
| `root_admin` | full CRUD | full CRUD | full CRUD | read | full CRUD |
| `guardian` | none | read-only | read-only | read-only | read + review (`PUT`) only |
| `truck_driver` | none | read | read | read | read |

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

## Current status

Implemented and **verified end to end against real infrastructure**, not just written — Docker
was available in the session that built this, so every layer below was actually run, not left
as a "should work" claim:

- The hermetic test suite passes (`pytest` from this directory, 27 tests, `mongomock-motor`
  backing Beanie — no real Mongo needed).
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
