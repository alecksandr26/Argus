#!/usr/bin/env bash
# One-time setup for real road-following routes: downloads a Mexico OSM extract from Geofabrik
# (~150MB) and preprocesses it into ../osrm-data/ (gitignored) via the official osrm/osrm-backend
# image's extract/partition/customize pipeline. This is what SIMULATOR_USE_OSRM=true needs
# osrm_client.py's queries to actually succeed against -- see the module README's "Optional:
# real road-following routes via OSRM" section.
#
# The full country extract is used, not a smaller regional one, because fleet.py's
# ROUTE_TEMPLATES span long-haul corridors across the whole country (e.g. Tijuana<->Mexicali in
# the north, Puebla<->Veracruz in the south-center) -- a regional extract wouldn't cover them.
#
# Takes several minutes on a modern machine; only needs to be run once (subsequent
# `docker compose --profile osrm up` runs reuse the preprocessed data in ./osrm-data/).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DATA_DIR="$SCRIPT_DIR/../osrm-data"
PBF_NAME="mexico-latest.osm.pbf"
PBF_PATH="$DATA_DIR/$PBF_NAME"
OSRM_BASENAME="mexico-latest"

mkdir -p "$DATA_DIR"

if [ ! -f "$PBF_PATH" ]; then
  echo "Downloading Mexico OSM extract from Geofabrik (~150MB)..."
  curl -L --fail -o "$PBF_PATH" "https://download.geofabrik.de/north-america/${PBF_NAME}"
else
  echo "Reusing existing extract at $PBF_PATH"
fi

echo "Extracting (car profile)..."
docker run --rm -t -v "$DATA_DIR:/data" osrm/osrm-backend \
  osrm-extract -p /opt/car.lua "/data/${PBF_NAME}"

echo "Partitioning..."
docker run --rm -t -v "$DATA_DIR:/data" osrm/osrm-backend \
  osrm-partition "/data/${OSRM_BASENAME}.osrm"

echo "Customizing..."
docker run --rm -t -v "$DATA_DIR:/data" osrm/osrm-backend \
  osrm-customize "/data/${OSRM_BASENAME}.osrm"

echo
echo "Done. Start OSRM with:"
echo "  docker compose --profile osrm up -d osrm"
echo "Then enable it for the simulator with SIMULATOR_USE_OSRM=true."
