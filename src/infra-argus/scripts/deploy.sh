#!/usr/bin/env bash
# One command: build + test the backend image, deploy all four stacks, build the UI against the
# deployed site URL, upload it, and smoke-test /health. Extra args go to `cdk deploy`
# (e.g. `npm run deploy -- -c alertEmail=you@example.com`).
source "$(dirname "$0")/lib.sh"
require aws docker node npm

# One version for both halves (route guardian contracts G6): the backend reports it on /health
# (ARGUS_VERSION), the UI is built with it (VITE_GIT_SHA) and warns on Fleet when they differ.
# "-dirty" marks a build from uncommitted changes.
VERSION="$(git -C "$SRC_DIR" rev-parse --short HEAD 2>/dev/null || echo dev)"
if [ "$VERSION" != dev ] && [ -n "$(git -C "$SRC_DIR" status --porcelain 2>/dev/null)" ]; then
  VERSION="$VERSION-dirty"
fi
echo "==> Version $VERSION"

echo "==> Building backend-argus (runs its pytest gate)"
# The Lambda wrapper image (docker/backend-lambda) is FROM this image, so it inherits the
# ARGUS_VERSION env the backend Dockerfile sets from this build arg.
docker build --build-arg "ARGUS_VERSION=$VERSION" -t argus/backend-argus:latest "$SRC_DIR/backend-argus"

echo "==> Deploying stacks"
cd "$INFRA_DIR"
# The Lambda image is built FROM argus/backend-argus:latest; CDK only hashes the wrapper folder,
# so the image id is what makes a backend change actually reach the Lambda.
CTX=(-c "backendImageId=$(docker image inspect -f '{{.Id}}' argus/backend-argus:latest)")
# Accounts with a Lambda concurrency limit of 10 can't reserve any (AWS keeps 10 unreserved).
LIMIT="$(aws lambda get-account-settings --query AccountLimit.ConcurrentExecutions --output text || true)"
if [ "$LIMIT" -lt 12 ] 2>/dev/null; then
  echo "NOTE: Lambda concurrency limit is $LIMIT; deploying the backend without reserved concurrency."
  CTX+=(-c backendReservedConcurrency=none)
fi
[ -n "${ROOT_ADMIN_EMAIL:-}" ] && CTX+=(-c "rootAdminEmail=$ROOT_ADMIN_EMAIL")
[ -n "${ALERT_EMAIL:-}" ] && CTX+=(-c "alertEmail=$ALERT_EMAIL")
# CLI args come last, so `-c ...` on the command line overrides .env.
npx cdk deploy ArgusNetwork ArgusSimulator ArgusApp ArgusGuardrails "${CTX[@]}" "$@"

SITE_URL="$(stack_output ArgusApp SiteUrl)"
BUCKET="$(stack_output ArgusApp SiteBucketName)"
DIST_ID="$(stack_output ArgusApp DistributionId)"

echo "==> Building ui-argus for $SITE_URL"
# Same origin as the API (CloudFront routes /api/* to the backend), so no CORS needed.
npm --prefix "$SRC_DIR/ui-argus" install --no-audit --no-fund
VITE_API_BASE_URL="$SITE_URL" VITE_GIT_SHA="$VERSION" npm --prefix "$SRC_DIR/ui-argus" run build

echo "==> Uploading UI"
aws s3 sync "$SRC_DIR/ui-argus/dist/" "s3://$BUCKET/" --delete
aws cloudfront create-invalidation --distribution-id "$DIST_ID" --paths '/*' >/dev/null

echo "==> Smoke test"
HEALTH=""
for i in 1 2 3 4 5 6; do
  if HEALTH="$(curl -fsS "$SITE_URL/health")"; then break; fi
  HEALTH=""
  echo "  waiting for the backend cold start ($i)..."; sleep 5
done
echo "  backend /health: ${HEALTH:-no answer}   (check again any time: curl -s $SITE_URL/health)"
echo "  UI version:      $VERSION"
case "$HEALTH" in
  *"\"version\":\"$VERSION\""* | *"\"version\": \"$VERSION\""*) echo "  backend and UI versions match" ;;
  *) echo "WARNING: /health doesn't report version $VERSION; the backend may be an older build than the UI." ;;
esac

OSRM_BUCKET="$(stack_output ArgusSimulator OsrmBucketName)"
if [ -z "$(aws s3 ls "s3://$OSRM_BUCKET/mexico-latest.osrm" 2>/dev/null)" ]; then
  echo "NOTE: no OSRM data uploaded yet; simulated trucks will drive straight lines. Run: npm run osrm:upload"
fi

echo
echo "Argus is live at: $SITE_URL"
