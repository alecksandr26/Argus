#!/usr/bin/env bash
# R10 manual panic button: stop every simulator task and throttle the backend to 0 concurrency
# (every request fails fast, nothing is billed). `npm run unkill` restores it.
source "$(dirname "$0")/lib.sh"
require aws

FN="$(stack_output ArgusApp BackendFunctionName)"
CLUSTER="$(stack_output ArgusSimulator ClusterName)"

for arn in $(aws ecs list-tasks --cluster "$CLUSTER" --started-by argus-simulator --desired-status RUNNING --query 'taskArns[]' --output text); do
  aws ecs stop-task --cluster "$CLUSTER" --task "$arn" --reason "Argus manual kill" >/dev/null
  echo "stopped $arn"
done
aws lambda put-function-concurrency --function-name "$FN" --reserved-concurrent-executions 0 >/dev/null
echo "Backend throttled (concurrency 0). Run 'npm run unkill' to restore."
