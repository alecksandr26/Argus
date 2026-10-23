# CLAUDE.md — it-argus

This file explains why `it-argus` is built the way it is. See `README.md` in this directory for
practical "how do I run this" instructions, and the top-level `CLAUDE.md` for how this fits the
rest of Argus.

## What this is

`src/backend-argus` and `src/ui-argus` each have their own real unit-test suites, but both mock
the other side away — the backend's tests never render a browser, and the frontend's tests mock
`fetch` rather than talking to a real backend. Neither ever actually exercises the seam between
them. `it-argus` is that seam test: **Playwright** browser tests that drive the real `ui-argus`
dev server against the real `backend-argus` + MongoDB, together.

Login was the first real thing connecting the two sides (see `src/backend-argus/CLAUDE.md`'s
"Root admin bootstrap" and `src/ui-argus/CLAUDE.md`'s "Auth" sections), so it's the natural first
thing to prove end to end with a real browser rather than adding this harness later against a
much larger, harder-to-bootstrap surface with no working example to build from.

## Docker Compose: its own standalone stack, not an overlay

`docker-compose.yml` here defines a **complete**, self-contained stack — its own `mongo`,
`backend-argus`, `ui-argus`, and the `it-argus` test runner itself — rather than layering onto
the repo-root `docker-compose.yml`. This is a deliberate choice, not an oversight: this stack's
needs are already diverging from the dev stack's (a clean database every run, no published host
ports, healthchecks the dev stack doesn't need) and will keep diverging as more is added here, so
keeping the two files independent is worth the topology duplication it costs. Concretely:

- **Its own named Mongo volume**, never the dev stack's — every run starts from a clean
  database, which matters directly for `tests/auth.spec.ts` asserting on the root_admin
  bootstrap step (a stale, already-bootstrapped database would defeat that test's purpose).
- **No published host ports** on `mongo`/`backend-argus`/`ui-argus` — nothing outside this
  compose project needs to reach them, and staying off `8000`/`5173` means this stack can run
  alongside an already-running dev `docker compose up` without a port collision.
- **A real networking bug this actually hit, not a design guessed upfront**: the first version
  of this file gave `it-argus` its own network attachment and pointed Playwright at the internal
  DNS name `http://ui-argus:5173` — reasonable-looking, but it silently broke login. `src/utils/
  crypto.ts`'s `sha256Hex` (the real login path) needs `crypto.subtle`, which browsers only
  expose in a "secure context" — HTTPS, or specifically the host `localhost` — and
  `http://ui-argus:5173` is neither, so every login attempt failed with "Could not sign in"
  and no other visible clue. The actual fix: `it-argus` uses `network_mode: "service:ui-argus"`
  — it shares `ui-argus`'s network namespace instead of getting its own, so `http://localhost:5173`
  (a genuinely secure context) resolves straight to it, while still keeping the same DNS
  visibility `ui-argus` already has to reach `http://backend-argus:8000` for its own API calls.
  `backend-argus`'s `CORS_ORIGINS` is set to `http://localhost:5173` for the same reason — that's
  the `Origin` header the browser actually presents once it loads the page this way.
- **Real `healthcheck:` blocks**, which neither `backend-argus`'s nor `ui-argus`'s own
  compose files define today (this file doesn't touch those — it no longer depends on them).
  `backend-argus`'s check calls `GET /health` via Python's own `urllib` (no `curl`/`wget`
  guaranteed present in `python:3.12-slim-bookworm`); `ui-argus`'s dev-server check uses Node's
  built-in `http` module the same way, for the same reason on `node:22-alpine`. `it-argus`'s
  `depends_on: condition: service_healthy` waits on both before Playwright ever tries to
  navigate anywhere.
- **Bootstrap credentials left at their checked-in defaults** (`admin@argus.dev` /
  `changeme123`, matching `src/backend-argus`'s own defaults) — `tests/auth.spec.ts` logs in
  with exactly these, so there's no extra credential-passing between this file and the spec.

## Playwright image/version pinning

`Dockerfile` uses `mcr.microsoft.com/playwright:v1.49.1-jammy` — Playwright's own image with
Chromium/Firefox/WebKit preinstalled, pinned to **the exact same** version as
`@playwright/test` in `package.json`. This isn't cosmetic: the image's bundled browser builds
are tied to a specific Playwright release, and letting the two drift is a real, common failure
mode (the test runner expects a browser revision the image doesn't actually have installed).
Bump both together, never just one.

## Test conventions

`playwright.config.ts`'s `baseURL` defaults to `http://localhost:5173`, and — thanks to
`network_mode: "service:ui-argus"` above — that default is already correct for the containerized
run too, not just for running `npx playwright test` locally against an already-running dev
stack; `IT_BASE_URL` remains available as an override for a differently-shaped setup. Specs use
`page.getByLabel`/`getByRole` the same way `ui-argus`'s own Vitest+RTL tests do, rather than CSS
selectors, so a spec here and its Vitest counterpart (if one exists for the same flow) stay
aligned in what they consider "the same element" — including preferring `getByRole('heading', …)`
over a bare `getByText(...)` for a page title, since the same text often also appears in the
sidebar nav link and a plain text match doesn't disambiguate between the two (hit directly
writing `tests/auth.spec.ts`, not a hypothetical).

## Reporting

`playwright.config.ts`'s `reporter` is `[['list'], ['html', ...]]` — console output plus a
browsable HTML report (`open: 'never'`, since the Docker Compose run has no display to pop a
browser tab into). `docker-compose.yml` bind-mounts `./playwright-report:/app/playwright-report`
on the `it-argus` service specifically so that report survives
`--abort-on-container-exit` tearing the container down — without the mount, the report is
generated inside the container and then lost the moment it exits. See README.md's "Viewing the
report" for how to open it (`npx playwright show-report playwright-report`).

## Worker count

`playwright.config.ts` caps `workers: 4` rather than leaving Playwright's default (half the
machine's CPU cores — 6 on the box this was tuned on). The real bottleneck under parallel load
isn't browser count, it's `backend-argus` running as a single Uvicorn process: every login/user-
create does a CPU-bound bcrypt hash (see that module's CLAUDE.md's "Auth design"), and enough
concurrent requests queue behind that one process to occasionally exceed even the bumped
`expect.timeout` (10s, up from Playwright's 5s default — see the inline comment). 4 workers
measurably reduced that flakiness as the suite grew past 11 specs; it doesn't eliminate flakiness
from *external* load (another `docker compose up` stack — the repo-root one, or either module's
own — genuinely competing for the same host's CPU/memory) — that's environmental, not a bug in
this suite, and reran clean once that contention wasn't present.

## Final-tweaks specs and `IT_FINAL_TWEAKS_UI`

Since the final tweaks (`docs/designs/final-tweaks-plan.md`, contracts in
`final-tweaks-contracts.md`), every alert and GPS point a spec needs is reported **the way a truck
reports it**: `POST /api/devices/me/report` with that truck's own device API key
(`helpers.ts`'s `rotateDeviceKey`, `postReport`/`postReportOk`, `reportPoint`, `alertEvent`,
`okEvent`). That's the only device write left; the old `POST /api/routes/{id}/status` /
`POST /api/alerts` paths, the panic button, the grip sensor and the per-alert review/AlertTriage
flow are gone, and so are the specs for them. Fleet setup (trucks, drivers, an `in_progress`
route with its `actual_departure` set in the past, users of each role) goes over REST through
`setupActiveTruck`/`createUserAndLogin`, so each spec's UI half starts from known data.

| Spec | Covers |
|---|---|
| `auth.spec.ts` | root_admin bootstrap login, wrong password, deep link to `/login` and back, sign-out |
| `users.spec.ts` | Access screen: root_admin creates an admin, the admin creates a guardian only (also enforced over REST) |
| `trucks.spec.ts`, `drivers.spec.ts` | create through Fleet/Drivers; a guardian sees both read-only |
| `live-ops.spec.ts` | a route created through the Routes screen whose reported critical shows on the map and in the feed; a no-face critical (`scores: null`) closed by a guardian with a note; an admin refused the close (403); a guardian de-escalating an alert; a DLQ replay labelled "Recovered after lost connection" |
| `incidents.spec.ts` | grouping (15 min window by event time, escalation joins its medium, duplicates, a late alert joining the right incident), the driver OK never closing anything, the tier-ordered `/api/live` feed, closing (note required for a critical, guardian/root_admin only, route state dropping with each close, 409 on a second close), a late alert never joining a closed incident, and the UI tiers + close dialog |
| `route-stop.spec.ts` | Stop route (reason required with the pinned 422 text, root_admin/admin only), every later report rejected `route_finished` (points, event, driver OK, DLQ replay); Complete trip the same; `no_route` before any route started; the Stop route dialog |
| `positions.spec.ts` | alert positions (live, last known with its age, unknown), last-known GPS points in the path, points less than a second apart both stored with ms timestamps (C2.1 [sync 3]), and the UI position labels |

**`IT_FINAL_TWEAKS_UI`** (`docker-compose.yml` passes it through, default `0`). The final-tweaks
merge order puts the backend, simulator and this module before `ui-argus`'s new screens, so the
specs and spec halves that assert on the incident feed, the close/stop dialogs and the position
labels are skipped unless the run sets `IT_FINAL_TWEAKS_UI=1`. The API-level checks of the same
behaviour always run. The lead's integration step runs the full browser suite with it set once
T5's UI is in the stack.

**Selectors use only the UI test hooks pinned in C3.7 [sync 3]**, wrapped in `helpers.ts`:

- `tierRegion(page, 'Critical' | 'Medium' | 'Older than 1 h')`: the tier section by its region
  name (`Critical incidents`, ...), never the header text, which adds a count (`Older than 1 h ·
  1`).
- `incidentCard(scope, label)`: the incident card (`<article>`) holding an alert with that exact
  alert-type label. A spec acts on the right incident's own `Close incident` button, whatever order
  the cards come in, and never mistakes the route view's `Open`/`Closed` filter chips for incident
  state. `exact: true` keeps `Eyes closed` from matching `Head down, eyes closed`.
- `closeDialog(page)` / `getByRole('dialog', { name: 'Stop route' })`: the named dialogs.
- `closeConfirmation(page)`: the `role="status"` element containing `Closed HH:MM` that the UI
  shows after a successful close. A closed incident leaves the Open list; after a reload a spec
  checks it under the `Closed` filter chip.

Earlier versions of these specs used `getByText('Open', { exact: true })` (which matched the
route view's filter chip, so it passed whether or not the incident was open) and
`.first()` on `Close incident` (which assumed the critical card came first). Both are gone.

## Current status

**Run for real before the final tweaks**: `docker compose up --build --abort-on-container-exit`
booted the full stack and every spec of that time passed. Getting the original auth suite running
surfaced two real bugs (see "Docker Compose" above for the network/secure-context one), and the
old live-ops spec found three `getByLabel`/`getByText` substring false positives, all fixed with
`exact: true`.

**Final tweaks, verified so far**: the specs type-check under `tsc --strict`, and
`playwright test --list` lists all of them (29 with `IT_FINAL_TWEAKS_UI=1`). The API-level specs
were run against team T4's round-2 backend with a throwaway Mongo and passed. Not yet run: the
browser halves (`IT_FINAL_TWEAKS_UI=1`) against T5's UI, and the new millisecond-timestamp
positions test, which needs T4's round-3 backend (C2.1 [sync 3]). Both are the lead's
integration step.

**Still not covered**: a full round trip through the real ingestion chain (`cv-argus` → ESP32 →
backend). The specs report as a truck over the same HTTP endpoint the ESP32 uses, but no real
device is involved; `src/tests/edge_chain` covers the Pi↔ESP32 side. See `docs/roadmap.md`
section 7 for the broader edge-to-cloud gap, which is outside this module's scope (the
`ui-argus`↔`backend-argus` seam only).
