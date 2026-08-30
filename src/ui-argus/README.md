# ui-argus

The Argus web frontend — a React + TypeScript app (Vite) serving the two MVP roles,
**Control Tower** (live monitoring) and **Administration / Logistics** (fleet/driver/route
management). See `CLAUDE.md` in this directory for why it's built this way, and the
top-level `CLAUDE.md` for how this fits the rest of Argus.

**Current status: all six mockup screens ported, running on fake data.** Login, the live-ops
dashboard, alert triage, and the Fleet/Drivers/Routes CRUD screens are real components that
read from `src/data/fixtures.ts` — search filters, row selection, edit panels and create forms
all work against local state. The live-ops dashboard has a real interactive map
(**react-leaflet** + OpenStreetMap tiles, keyless) with a truck marker per live-status row. UI
copy is in English (the design canvas is in Spanish; translated on request). What's **not**
there: auth, role-gating, and any real backend call.
None of it has been `npm install`ed or built yet either — see CLAUDE.md's "Current status",
and **`INTEGRATION.md` for the checklist of where backend-connectivity code needs to land**
(per screen, plus the cross-cutting gaps — API client, auth, real-time strategy).

## Quick start (Docker — recommended)

No local Node/npm install needed; everything runs in the container.

```bash
cp .env.example .env
docker compose up --build
```

Open http://localhost:5173. Source is bind-mounted, so edits under `src/` hot-reload without
rebuilding the image.

## Quick start (local Node, no Docker)

Needs Node 22+ and npm on your machine.

```bash
cp .env.example .env
npm install
npm run dev
```

## Other commands

| Command | What it does |
|---|---|
| `npm run dev` | Vite dev server with hot reload |
| `npm run build` | Type-checks (`tsc -b`) then produces a production bundle in `dist/` |
| `npm run preview` | Serves the `dist/` bundle locally, to sanity-check a production build |
| `npm run lint` | ESLint over the whole project |

## Building the production image

The Dockerfile's default target (`prod`) builds the app and serves the static bundle via
nginx — this is what's meant to sit next to the FastAPI backend in the planned cloud Docker
Compose stack (not yet built), not something you run for day-to-day development:

```bash
docker build -t argus/ui-argus:prod .
docker run --rm -p 8080:80 argus/ui-argus:prod
```

## Config

| Variable | Default | Purpose |
|---|---|---|
| `VITE_API_BASE_URL` | `http://localhost:8000` | Base URL of the FastAPI backend. Unused for now — that backend doesn't exist yet. |

## Troubleshooting

- **`docker compose up` fails looking for `.env`** — run `cp .env.example .env` first;
  `docker-compose.yml` expects the file to exist even though nothing in it is required yet.
- **Port 5173 already in use** — another Vite dev server (or a previous `docker compose up`)
  is still running; stop it, or change the host-side port in `docker-compose.yml`'s `ports:`.
- **Edits under `src/` aren't showing up in the Docker dev server** — this shouldn't happen
  (`vite.config.ts` forces polling specifically so bind-mount edits are always picked up); if
  it does, restart the container rather than digging into it first.
- **`npm ci` instead of `npm install`** — the Dockerfile deliberately uses `npm install`
  because no `package-lock.json` is committed yet (see the Dockerfile's comment on this). Once
  one exists and is committed, switch the Dockerfile's `deps` stage to `npm ci` for
  reproducible installs.
