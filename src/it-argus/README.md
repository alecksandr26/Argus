# it-argus

Integration tests: **Playwright** browser tests driving the real `ui-argus` dev server against
the real `src/backend-argus` + MongoDB, together — not each module's own unit tests, which mock
the other side away. See `CLAUDE.md` in this directory for why it's built this way, and the
top-level `CLAUDE.md` for how this fits the rest of Argus.

## Quick start (Docker — the only supported way to run this)

This module's `docker-compose.yml` is fully self-contained: its own `mongo`, `backend-argus`,
and `ui-argus` (in addition to the `it-argus` test runner itself), so it needs nothing else
already running.

```bash
docker compose up --build --abort-on-container-exit
```

`--abort-on-container-exit` stops the whole stack once `it-argus` finishes, so this command
exits cleanly with the test result rather than leaving `mongo`/`backend-argus`/`ui-argus`
running in the background.

No host ports are published by this compose file — it's safe to run alongside an already-running
dev stack (the repo-root `docker compose up`, or either module's own) without a port collision.

## Running locally against an already-running stack (no Docker for this module)

Needs Node 22+ and npm, plus Playwright's browsers installed once (`npx playwright install`).

```bash
npm install
npx playwright install --with-deps chromium
IT_BASE_URL=http://localhost:5173 npm run test   # against the repo-root `docker compose up`
```

## Viewing the report

Every run produces an HTML report (`playwright.config.ts`'s `html` reporter), not just the
console `list` output. The Docker run mounts it out to `./playwright-report/` on the host (see
`docker-compose.yml`), so after `docker compose up --build --abort-on-container-exit` finishes:

```bash
npx playwright show-report playwright-report
```

Running locally (no Docker for this module) writes to the same `./playwright-report/` directly,
no extra step needed. A failed test also gets a trace (`trace: 'retain-on-failure'`) — open one
with `npx playwright show-trace test-results/<test-name>/trace.zip`.

## What's covered

- `tests/auth.spec.ts` — the login flow this module was built alongside: logging in as the
  bootstrapped root admin, a wrong-password error, an unauthenticated deep link redirecting to
  `/login` and back after signing in, and sign-out re-protecting a route.
- `tests/users.spec.ts` — `POST /api/users` through the real Access screen: root_admin creates
  an `admin` account (who can then log in and finds their own Access panel locked to creating
  guardians only), that admin creates a `guardian` (who is then bounced off `/access` entirely),
  and a direct REST call proving the guardian-only scoping is enforced server-side, not just by
  the UI disabling the role picker (an `admin` JWT POSTing `role: "admin"` gets a real `403`).
- `tests/trucks.spec.ts` — `POST /api/trucks` through the Fleet screen: root_admin creates a
  truck, and a guardian sees the same screen read-only (no "Add truck" button, disabled fields,
  no Save button on an existing truck).
- `tests/drivers.spec.ts` — the same shape as `trucks.spec.ts`, for `POST /api/drivers`/the
  Drivers screen.
- `tests/live-ops.spec.ts` — Live operations and the route/alert view, fed the way a truck feeds
  them: every alert is reported with the truck's own device key through
  `POST /api/devices/me/report` (`helpers.ts`'s `postReport`/`alertEvent`), the only device
  write. Covers: a route created through the Routes screen whose critical alert shows as a live
  map marker, a no-face critical (no scores) whose incident a guardian closes with a note, an
  admin being refused the close (403), and a guardian de-escalating an alert.
- `tests/incidents.spec.ts` — incident grouping (15 min window by event time, escalations,
  duplicates, the driver OK never closing anything, the tier-ordered `/api/live` feed) and
  closing (a note required for a critical, guardian/root_admin only, route state dropping with
  each close, a late alert never joining a closed incident).
- `tests/route-stop.spec.ts` — "Stop route" (reason required, root_admin/admin only), then every
  report for that route rejected `route_finished` (points, a new event, the driver OK, a
  dead-letter replay); "Complete trip" the same; `no_route` before any route started.
- `tests/positions.spec.ts` — alert positions (live, last known with its age, unknown) and
  last-known GPS points in the path.

**`IT_FINAL_TWEAKS_UI`.** The incident feed, the close/stop dialogs and the position labels land
in `ui-argus` after the backend does (final-tweaks merge order: backend + simulator + this module
first, the UI last). Specs, or spec halves, that assert on those screens are skipped unless the
run sets `IT_FINAL_TWEAKS_UI=1` (`IT_FINAL_TWEAKS_UI=1 docker compose up --build
--abort-on-container-exit`); the API-level checks of the same behaviour always run. Their
selectors are written from the UI strings agreed in the contracts (`docs/designs/
final-tweaks-contracts.md`, C3.7) and may need adjusting to the merged UI's exact DOM.

Every spec generates its own test data (unique emails/plate numbers/license numbers per run —
see `tests/helpers.ts`'s `uniqueSuffix()`) rather than hardcoding fixed values, since this
stack's Mongo volume isn't wiped between reruns without `docker compose down -v` — a hardcoded
value would 409 on the second run. Extend this suite alongside future backend-connected
`ui-argus` screens (see that module's `INTEGRATION.md`) — each new one wired up is a natural
candidate for a new spec here.

## Troubleshooting

- **Services never become healthy / `it-argus` times out waiting on `depends_on`** — check
  `docker compose logs backend-argus` / `ui-argus` first; a broken build or a Mongo connection
  issue shows up there before it shows up as a Playwright timeout.
- **Login fails with a generic "Could not sign in" and nothing else** — this is what
  `crypto.subtle` being unavailable looks like (the real login path needs it, and browsers only
  expose it in a secure context — HTTPS, or specifically the host `localhost`). If you've changed
  `docker-compose.yml`'s `network_mode`/`baseURL` wiring, you've likely pointed the browser at a
  non-`localhost` hostname over plain HTTP again — see `CLAUDE.md`'s "Docker Compose" section for
  why this file is built the way it is (a real bug hit here, not a hypothetical).
- **Tests fail with a connection-refused-looking error** — double-check `IT_BASE_URL`; the
  default (`http://localhost:5173`) is correct for both the Docker Compose run and running
  locally against the repo-root dev stack, so an override is only needed for a differently-shaped
  setup.
