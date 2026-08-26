# syntax=docker/dockerfile:1

# Alpine (musl), unlike src/cv-argus's Debian base: this is a plain Vite/React frontend
# with no MediaPipe/TensorFlow-style manylinux-only wheels to worry about, so the smaller
# Node image is the right default here.
FROM node:22-alpine AS base
WORKDIR /app

# --- deps -----------------------------------------------------------------
# Separate layer so `npm install` is only re-run when package*.json actually change, not
# on every source edit. No package-lock.json is committed yet (this scaffold has never
# been `npm install`ed) — `npm ci` would fail without one, so this uses `npm install` for
# now. Once a lockfile exists and is committed, switch this to `npm ci` for reproducible,
# faster installs.
FROM base AS deps
COPY package.json ./
RUN npm install

# --- dev --------------------------------------------------------------
# Target used by docker-compose.yml for local development: source is bind-mounted over
# this layer (see that file), so Vite's dev server sees edits immediately and this image
# only has to provide node_modules and the toolchain, not a copy of src/ that would
# instantly go stale.
FROM deps AS dev
COPY . .
EXPOSE 5173
CMD ["npm", "run", "dev"]

# --- build ------------------------------------------------------------
# Produces the static bundle (dist/) consumed by the "prod" stage below.
FROM deps AS build
COPY . .
RUN npm run build

# --- prod -------------------------------------------------------------
# Default target (`docker build .` with no --target): a static bundle behind nginx, meant
# to sit next to the FastAPI backend in the planned cloud Docker Compose stack (see the
# top-level CLAUDE.md's "Planned end-to-end system architecture") — not the image used
# for day-to-day development, that's "dev" above.
FROM nginx:1.27-alpine AS prod
COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
CMD ["nginx", "-g", "daemon off;"]
