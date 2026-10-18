#!/usr/bin/env bash
# Delete the OSRM data from S3 between demo periods (saves ~$0.20/month). The simulator still
# works without it, with straight-line routes. `npm run osrm:upload` puts it back.
source "$(dirname "$0")/lib.sh"
require aws
BUCKET="$(stack_output ArgusSimulator OsrmBucketName)"
aws s3 rm "s3://$BUCKET/" --recursive
echo "Removed OSRM data from s3://$BUCKET"
