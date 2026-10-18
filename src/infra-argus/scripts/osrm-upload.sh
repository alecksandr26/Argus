#!/usr/bin/env bash
# Upload the preprocessed OSRM Mexico graph (~8.6 GB, from src/simulator-argus/scripts/setup_osrm.sh)
# to the simulator's S3 bucket. The raw .osm.pbf isn't needed at runtime and is skipped.
# Override the source with OSRM_DATA_DIR=/path/to/osrm-data.
source "$(dirname "$0")/lib.sh"
require aws

DATA_DIR="${OSRM_DATA_DIR:-$SRC_DIR/simulator-argus/osrm-data}"
[ -f "$DATA_DIR/mexico-latest.osrm" ] || {
  echo "No preprocessed data at $DATA_DIR. Run src/simulator-argus/scripts/setup_osrm.sh first," >&2
  echo "or point OSRM_DATA_DIR at an existing osrm-data folder." >&2
  exit 1
}
BUCKET="$(stack_output ArgusSimulator OsrmBucketName)"
aws s3 sync "$DATA_DIR/" "s3://$BUCKET/" --exclude '*.pbf'
echo "Uploaded to s3://$BUCKET (~\$0.20/month while it's there; npm run osrm:remove deletes it)"
