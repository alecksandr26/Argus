# Design decision — frontend map library & coordinate handling

**Status:** accepted · **Date:** 2026-08-29 · **Applies to:** `src/ui-argus`

## Context

The Control Tower screen ("Live operations") has to plot live truck positions on
a geographic map. Two questions had to be settled:

1. **Which map technology.** The project's two technical-definition documents
   disagree here (see `docs/document/borrador-proyecto-modular-argus.md`, "dos
   documentos de definición técnica"): the code-real definition (`CLAUDE.md` +
   `docs/designs/semantic-design`) says **React + react-leaflet**, while the
   AWS-oriented `Argus_Definicion_Tecnica.docx.pdf` says **Amazon Location
   Service or Google Maps API**. This document resolves that in favour of the
   code-real definition.
2. **How coordinates flow from the backend to the map.** The backend does not
   exist yet, so its coordinate serialization format is unknown, and it may not
   match either the app's internal shape or what the map library expects.

## Decision 1 — `react-leaflet` + OpenStreetMap tiles

The map uses **`react-leaflet` 5.x** (the only release line compatible with the
project's React 19) over **Leaflet**, with the **standard OpenStreetMap raster
tile layer** (`https://tile.openstreetmap.org/{z}/{x}/{y}.png`).

- Implemented in `src/ui-argus/src/components/FleetMap.tsx`; consumed by
  `src/ui-argus/src/pages/LiveOps.tsx`.
- Truck markers are `L.divIcon` (an HTML/CSS dot + plate chip), never Leaflet's
  default image marker.
- OSRM route-line rendering stays **deferred** ("mejora futura") — the map
  currently shows only point positions, consistent with the MVP scope in the
  borrador ("mapa mostrando geolocalización y origen/destino capturados; sin
  ruteo calculado por OSRM en el MVP").

### Why not Google Maps / Amazon Location Service

- **Cost & credentials.** Google Maps' "free" tier still requires a Google Cloud
  project with **billing enabled** and a Maps JavaScript API key shipped in the
  browser bundle. Amazon Location Service likewise requires AWS credentials and
  is billed per request. Leaflet + OpenStreetMap needs **no API key, no account,
  no billing** — appropriate for a titulación project.
- **Consistency with the committed architecture.** `CLAUDE.md` and
  `docs/designs/semantic-design.drawio.xml` (which has a "Leaflet Library" node)
  already name react-leaflet. Choosing Google/AWS would be an unexplained
  deviation, and the grading criteria (`docs/criteria/`, "Arquitectura y
  Programación") reward *justified* technology choices.
- **"Local rendering."** Both Leaflet and Google render in the browser and fetch
  map tiles from a remote CDN — neither is meaningfully "more local". If OSM's
  tile server ever rate-limits this deployment, CARTO is a keyless drop-in
  replacement (URL documented in `FleetMap.tsx`); self-hosting tiles via a
  container in the planned Docker Compose stack is the offline path.
- **Marker-image bug.** Using `L.divIcon` (not a default marker) sidesteps the
  well-known Leaflet-with-a-bundler broken-marker-image problem entirely.

### Consequences

- New runtime dependencies: `leaflet`, `react-leaflet`; dev dependency
  `@types/leaflet`. First real `npm install` will pin them into the
  (not-yet-committed) `package-lock.json`.
- `react-leaflet`'s `<MapContainer>` `center`/`zoom`/`bounds` are init-only (not
  reactive); `<Marker>` `position` *is* reactive. When live data replaces the
  fixtures, markers move on their own, but a `useMap()` child effect is needed to
  re-fit the viewport as the fleet moves.

## Decision 2 — normalize coordinates at the API boundary

The app has **one canonical coordinate shape**:
`Coordinates { lat: number; lon: number }` (`src/ui-argus/src/types.ts`) — WGS84
decimal degrees, spelled `lon` (not `lng`) to match the ER model's field names.
Every screen and `FleetMap` consume only this shape.

Two helpers in `src/ui-argus/src/utils/geo.ts` bridge the two edges:

- **`toLatLng(c)`** — `Coordinates` → the `[lat, lng]` tuple Leaflet expects.
  Components call this instead of hand-writing `[c.lat, c.lon]`.
- **`normalizeCoordinates(raw)`** — the **adapter** at the API boundary. It folds
  any of the following backend shapes into `Coordinates`, and throws on anything
  unrecognised or out of range:

  | Input | Origin |
  |---|---|
  | `{ lat, lon }` | ER-model spelling / current fixtures (passthrough) |
  | `{ lat, lng }` | the JS/Leaflet spelling |
  | `"lat,lon"` | a comma string |
  | `[lng, lat]` | a bare GeoJSON position — **longitude first** |
  | `{ type: "Point", coordinates: [lng, lat] }` | a GeoJSON `Point` geometry |

### Why

The planned backend is **FastAPI + MongoDB**. MongoDB's `2dsphere` geospatial
index stores points as **GeoJSON** (`[longitude, latitude]` — longitude first),
which is neither the `{lat,lon}` shape `types.ts` declares nor the `[lat,lng]`
order Leaflet wants. Doing the conversion in one place, at the boundary, keeps
that mismatch out of every component. This is recorded as **mandatory** for the
future API client in `src/ui-argus/INTEGRATION.md` (cross-cutting gap #1): every
`Alert.coordinates`, `Status_Route.current_coordinates`, and
`Route.destination_coordinates` from the API must pass through
`normalizeCoordinates`.

### Consequences

- `normalizeCoordinates` is currently unused — the fixtures are already
  `Coordinates`. It exists as the ready-to-wire boundary contract; the API client
  (`src/ui-argus/src/api/`, not built yet) is where it gets called.
- If the backend is later specified to return coordinates in a shape not in the
  table above, extend `normalizeCoordinates` rather than adding shape checks in
  components or screens.

## References

- `src/ui-argus/src/components/FleetMap.tsx`, `src/ui-argus/src/utils/geo.ts`
- `src/ui-argus/CLAUDE.md` ("Stack choices"), `src/ui-argus/INTEGRATION.md` (gap #1)
- `docs/designs/semantic-design.drawio.xml` ("Leaflet Library" node)
- `docs/document/borrador-proyecto-modular-argus.md` (the two-definitions tension)
- Top-level `CLAUDE.md` — "Planned end-to-end system architecture", frontend bullet
