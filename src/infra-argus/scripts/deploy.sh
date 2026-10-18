#!/usr/bin/env bash
# One command: build + test the backend image, deploy all four stacks, build the UI against the
# deployed site URL, upload it, and smoke-test /health. Extra args go to `cdk deploy`
# (e.g. `npm run deploy -- -c alertEmail=you@example.com`).
source "$(dirname "$0")/lib.sh"
require aws docker node npm

echo "==> Building backend-argus (runs its pytest gate)"
docker build -t argus/backend-argus:latest "$SRC_DIR/backend-argus"

echo "==> Deploying stacks"
cd "$INFRA_DIR"
npx cdk deploy ArgusNetwork ArgusSimulator ArgusApp ArgusGuardrails "$@"

SITE_URL="$(stack_output ArgusApp SiteUrl)"
BUCKET="$(stack_output ArgusApp SiteBucketName)"
DIST_ID="$(stack_output ArgusApp DistributionId)"

echo "==> Building ui-argus for $SITE_URL"
# Same origin as the API (CloudFront routes /api/* to the backend), so no CORS needed.
npm --prefix "$SRC_DIR/ui-argus" install --no-audit --no-fund
VITE_API_BASE_URL="$SITE_URL" npm --prefix "$SRC_DIR/ui-argus" run build

echo "==> Uploading UI"
aws s3 sync "$SRC_DIR/ui-argus/dist/" "s3://$BUCKET/" --delete
aws cloudfront create-invalidation --distribution-id "$DIST_ID" --paths '/*' >/dev/null

echo "==> Smoke test"
for i in 1 2 3 4 5 6; do
  if curl -fsS "$SITE_URL/health"; then echo; break; fi
  echo "  waiting for the backend cold start ($i)..."; sleep 5
done

OSRM_BUCKET="$(stack_output ArgusSimulator OsrmBucketName)"
if [ -z "$(aws s3 ls "s3://$OSRM_BUCKET/mexico-latest.osrm" 2>/dev/null)" ]; then
  echo "NOTE: no OSRM data uploaded yet; simulated trucks will drive straight lines. Run: npm run osrm:upload"
fi

echo
echo "Argus is live at: $SITE_URL"
