"""R5 — stop any Argus simulator task older than one session (MAX_MINUTES), every minute.

Independent of backend-argus on purpose: if the backend crashes, loses its Mongo session row, or
someone starts a task by hand, this still ends it. Measured from the task's `createdAt` (when
ECS accepted the start), the same moment the backend's countdown starts from.
"""
import os
from datetime import datetime, timedelta, timezone

import boto3

ecs = boto3.client("ecs")
CLUSTER = os.environ["CLUSTER"]
STARTED_BY = os.environ["STARTED_BY"]
MAX_MINUTES = int(os.environ["MAX_MINUTES"])


def handler(event, context):
    arns = ecs.list_tasks(cluster=CLUSTER, startedBy=STARTED_BY, desiredStatus="RUNNING")["taskArns"]
    if not arns:
        return {"stopped": []}
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=MAX_MINUTES)
    stopped = []
    for task in ecs.describe_tasks(cluster=CLUSTER, tasks=arns)["tasks"]:
        if task["createdAt"] <= cutoff:
            ecs.stop_task(
                cluster=CLUSTER,
                task=task["taskArn"],
                reason=f"Argus reaper: older than {MAX_MINUTES} minutes",
            )
            stopped.append(task["taskArn"])
    print({"running": len(arns), "stopped": stopped})
    return {"stopped": stopped}
