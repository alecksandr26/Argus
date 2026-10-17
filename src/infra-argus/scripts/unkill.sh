#!/usr/bin/env bash
# Undo `npm run kill` (or the automatic kill switch): restore backend concurrency to 2, or
# remove the reservation entirely with UNKILL_CONCURRENCY=none (low-limit accounts).
source "$(dirname "$0")/lib.sh"
require aws

FN="$(stack_output ArgusApp BackendFunctionName)"
if [ "${UNKILL_CONCURRENCY:-2}" = "none" ]; then
  aws lambda delete-function-concurrency --function-name "$FN"
  echo "Backend concurrency reservation removed."
else
  aws lambda put-function-concurrency --function-name "$FN" --reserved-concurrent-executions "${UNKILL_CONCURRENCY:-2}" >/dev/null
  echo "Backend concurrency restored to ${UNKILL_CONCURRENCY:-2}."
fi
