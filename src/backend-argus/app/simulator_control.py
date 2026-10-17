"""root_admin-only on/off control of the cloud fleet simulator.

The simulator (`src/simulator-argus`) and its OSRM sidecar run as one ECS Fargate Spot task per
session, defined in `src/infra-argus`'s SimulatorStack. This module starts, inspects, and stops
that task, and is where the backend's share of the hard cost guardrails lives (the full list,
R1–R10, is in `src/infra-argus/README.md`):

- R1: every session is exactly `simulator_session_minutes` (10) long. The start request has no
  duration field at all; the minutes are passed to the task by this module, and `status()`
  stops a task that is still alive past its `auto_off_at`.
- R2: at most one simulator task at a time — checked against both Mongo and ECS itself, so a
  task the backend lost track of still blocks a new one.
- R3: a monthly quota (`simulator_monthly_quota_minutes`, UTC calendar month), charged a full
  session up front on every start and never refunded on an early stop.

The ECS-side backstops (the in-container `timeout`, the 1-minute reaper Lambda, the budget
action) don't depend on this module working at all — see that README.

boto3 is synchronous, so every ECS call goes through `asyncio.to_thread`.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from fastapi import HTTPException, status

from app.config import Settings, settings
from app.models.simulator_session import SimulatorSession
from app.models.user import User

STARTED_BY = "argus-simulator"
SIMULATOR_CONTAINER = "simulator"

# ECS `lastStatus` values that mean "on its way up" vs "up". Anything else (DEACTIVATING,
# STOPPING, DEPROVISIONING, STOPPED) counts as stopped for the UI.
_STARTING_STATES = {"PROVISIONING", "PENDING", "ACTIVATING"}
_RUNNING_STATES = {"RUNNING"}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _aware(dt: datetime) -> datetime:
    """Mongo hands datetimes back naive (implicitly UTC); make them comparable to `_utcnow()`."""
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


def _month_start(now: datetime) -> datetime:
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


@dataclass
class SimulatorState:
    state: str  # "stopped" | "starting" | "running"
    session: Optional[SimulatorSession]
    quota_used_minutes: int


class SimulatorController:
    def __init__(self, ecs: Any, config: Settings = settings) -> None:
        self._ecs = ecs
        self._config = config

    # --- ECS -----------------------------------------------------------------------------

    async def _call(self, method: str, **kwargs: Any) -> dict:
        try:
            return await asyncio.to_thread(getattr(self._ecs, method), **kwargs)
        except Exception as exc:  # botocore errors, throttling, a budget-action deny, ...
            raise HTTPException(
                status.HTTP_502_BAD_GATEWAY, f"AWS ECS {method} failed: {exc}"
            ) from exc

    async def _live_task_arns(self) -> list[str]:
        """Every simulator task ECS still wants running, whether or not Mongo knows about it."""
        resp = await self._call(
            "list_tasks",
            cluster=self._config.simulator_ecs_cluster,
            startedBy=STARTED_BY,
            desiredStatus="RUNNING",
        )
        return list(resp.get("taskArns", []))

    async def _task_status(self, task_arn: str) -> Optional[str]:
        resp = await self._call(
            "describe_tasks", cluster=self._config.simulator_ecs_cluster, tasks=[task_arn]
        )
        tasks = resp.get("tasks", [])
        return tasks[0].get("lastStatus") if tasks else None

    async def _stop_task(self, task_arn: str, reason: str) -> None:
        await self._call(
            "stop_task",
            cluster=self._config.simulator_ecs_cluster,
            task=task_arn,
            reason=reason[:255],
        )

    # --- sessions / quota ----------------------------------------------------------------

    def auto_off_at(self, session: SimulatorSession) -> datetime:
        return _aware(session.started_at) + timedelta(minutes=session.requested_minutes)

    async def _quota_used_minutes(self, now: datetime) -> int:
        month_start = _month_start(now)
        sessions = await SimulatorSession.find_all().to_list()
        return sum(
            s.requested_minutes for s in sessions if _aware(s.started_at) >= month_start
        )

    async def _open_session(self) -> Optional[SimulatorSession]:
        return await SimulatorSession.find_one(SimulatorSession.stopped_at == None)  # noqa: E711

    async def _close(self, session: SimulatorSession, reason: str) -> None:
        session.stopped_at = _utcnow()
        session.stop_reason = reason
        await session.touch_and_save()

    # --- public API ----------------------------------------------------------------------

    async def status(self) -> SimulatorState:
        now = _utcnow()
        session = await self._open_session()
        state = "stopped"
        if session is not None:
            task_status = await self._task_status(session.task_arn)
            if task_status in _STARTING_STATES or task_status in _RUNNING_STATES:
                if now >= self.auto_off_at(session):
                    # Lazy R1 enforcement: the task outlived its session. The reaper Lambda would
                    # get it within a minute anyway; this just doesn't wait for it.
                    await self._stop_task(session.task_arn, "Argus simulator session time is up")
                    await self._close(session, "auto_off")
                else:
                    state = "starting" if task_status in _STARTING_STATES else "running"
            else:
                await self._close(session, "task_ended")
        if state == "stopped":
            session = None
        return SimulatorState(
            state=state, session=session, quota_used_minutes=await self._quota_used_minutes(now)
        )

    async def start(self, *, actor: User, truck_count: int, backend_url: str) -> SimulatorState:
        current = await self.status()
        if current.state != "stopped" or await self._live_task_arns():
            raise HTTPException(status.HTTP_409_CONFLICT, "The simulator is already on")

        minutes = self._config.simulator_session_minutes
        remaining = self._config.simulator_monthly_quota_minutes - current.quota_used_minutes
        if remaining < minutes:
            raise HTTPException(
                status.HTTP_429_TOO_MANY_REQUESTS,
                "Monthly simulator quota used up; it resets on the 1st (UTC)",
            )

        env = {
            "SIMULATOR_SESSION_MINUTES": str(minutes),
            "SIMULATOR_TRUCK_COUNT": str(truck_count),
            # Trips finish inside the session instead of the simulator's 20-minute default.
            "SIMULATOR_ROUTE_DURATION_MINUTES": str(minutes),
            "BACKEND_BASE_URL": self._config.simulator_backend_url or backend_url,
        }
        resp = await self._call(
            "run_task",
            cluster=self._config.simulator_ecs_cluster,
            taskDefinition=self._config.simulator_task_definition,
            count=1,
            startedBy=STARTED_BY,
            capacityProviderStrategy=[{"capacityProvider": "FARGATE_SPOT", "weight": 1}],
            networkConfiguration={
                "awsvpcConfiguration": {
                    "subnets": self._config.simulator_subnet_list,
                    "securityGroups": self._config.simulator_security_group_list,
                    "assignPublicIp": "ENABLED",
                }
            },
            overrides={
                "containerOverrides": [
                    {
                        "name": SIMULATOR_CONTAINER,
                        "environment": [{"name": k, "value": v} for k, v in env.items()],
                    }
                ]
            },
        )
        tasks = resp.get("tasks", [])
        if not tasks:
            failures = resp.get("failures", [])
            raise HTTPException(
                status.HTTP_502_BAD_GATEWAY, f"AWS ECS could not start the simulator: {failures}"
            )

        session = SimulatorSession(
            started_by=str(actor.id),
            started_at=_utcnow(),
            requested_minutes=minutes,
            truck_count=truck_count,
            task_arn=tasks[0]["taskArn"],
        )
        await session.insert()
        return SimulatorState(
            state="starting", session=session, quota_used_minutes=current.quota_used_minutes + minutes
        )

    async def stop(self) -> SimulatorState:
        session = await self._open_session()
        arns = set(await self._live_task_arns())
        if session is not None:
            arns.add(session.task_arn)
        for arn in arns:
            await self._stop_task(arn, "Turned off by root_admin")
        if session is not None:
            await self._close(session, "stopped_by_user")
        return await self.status()


def get_ecs_client() -> Any:
    import boto3  # lazy: a disabled deployment (local dev) never needs AWS credentials

    return boto3.client("ecs")


def get_simulator_controller() -> SimulatorController:
    if settings.simulator_control != "ecs":
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Simulator control is not configured on this deployment",
        )
    return SimulatorController(get_ecs_client())
