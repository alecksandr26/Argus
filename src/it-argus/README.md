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

## What's covered

`tests/auth.spec.ts` — the login flow this module was built alongside: logging in as the
bootstrapped root admin, a wrong-password error, an unauthenticated deep link redirecting to
`/login` and back after signing in, and sign-out re-protecting a route. Extend this suite
alongside future backend-connected `ui-argus` screens (see that module's `INTEGRATION.md`) —
each new one wired up is a natural candidate for a new spec here.

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
