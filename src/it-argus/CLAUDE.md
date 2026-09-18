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

## Current status

**Run for real, not just written**: `docker compose up --build --abort-on-container-exit` boots
the full stack (its own Mongo, `backend-argus`, `ui-argus`) and every spec passes — `auth.spec.ts`
(root-admin bootstrap login, wrong-password error, an unauthenticated deep link redirecting to
`/login` and back after signing in, sign-out re-protecting a route), `users.spec.ts` (root_admin
creates an admin who can log in and is scoped to guardians only; that admin creates a guardian
who is bounced off `/access`; a direct REST call proving the guardian-only scope is enforced
server-side, not just by the UI's disabled role picker), `trucks.spec.ts` and `drivers.spec.ts`
(create via the real Fleet/Drivers screens, plus a guardian seeing both read-only — no create
button, disabled fields, no Save button). Getting the original auth suite running surfaced two
real bugs this module's own existence was the point of catching (see "Docker Compose" above for
the network/secure-context one) — proof this harness earns its cost, not just a plan for one.

**Still not covered**: Routes, Live operations, and Alert triage have no spec yet — no full
round-trip proving an alert actually lands on a guardian's dashboard, for instance. Extending
this suite to those screens (the same real-backend, real-browser shape as `trucks.spec.ts`/
`drivers.spec.ts`) is the natural next step, the same way `ui-argus`'s own Vitest suite is meant
to grow alongside `src/api/*`.
