"""R8 — invoked (via SNS) when backend invocations spike: throttle the backend and stop the simulator.

Setting reserved concurrency to 0 makes every request to the backend fail fast with a throttle
(no compute billed). `npm run unkill` (scripts/unkill.sh) restores it.
"""
import os

import boto3

lam = boto3.client("lambda")
ecs = boto3.client("ecs")


def handler(event, context):
    lam.put_function_concurrency(
        FunctionName=os.environ["BACKEND_FUNCTION"], ReservedConcurrentExecutions=0
    )
    arns = ecs.list_tasks(
        cluster=os.environ["CLUSTER"], startedBy=os.environ["STARTED_BY"], desiredStatus="RUNNING"
    )["taskArns"]
    for arn in arns:
        ecs.stop_task(cluster=os.environ["CLUSTER"], task=arn, reason="Argus kill switch")
    print({"backend": "throttled", "stopped_tasks": arns})
    return {"stopped_tasks": arns}
