from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Request

from app.auth.dependencies import require_role
from app.config import settings
from app.models.common import Role
from app.models.user import User
from app.schemas.simulator import SimulatorStartRequest, SimulatorStatusOut
from app.simulator_control import SimulatorController, SimulatorState, get_simulator_controller

router = APIRouter(prefix="/api/simulator", tags=["simulator"])

# root_admin only, on every route — turning the cloud simulator on costs real money (see
# app/simulator_control.py and src/infra-argus/README.md's cost guardrails). The role check runs
# before `get_simulator_controller`, so other roles get 403 even on a deployment where simulator
# control is disabled (503).
_root_only = require_role(Role.ROOT_ADMIN)


def _out(controller: SimulatorController, state: SimulatorState) -> SimulatorStatusOut:
    minutes = settings.simulator_session_minutes
    quota = settings.simulator_monthly_quota_minutes
    remaining = max(quota - state.quota_used_minutes, 0)
    session = state.session
    return SimulatorStatusOut(
        state=state.state,
        session_minutes=minutes,
        started_at=session.started_at if session else None,
        auto_off_at=controller.auto_off_at(session) if session else None,
        truck_count=session.truck_count if session else None,
        quota_minutes=quota,
        quota_used_minutes=state.quota_used_minutes,
        quota_remaining_minutes=remaining,
        sessions_remaining=remaining // minutes if minutes > 0 else 0,
    )


@router.get("", response_model=SimulatorStatusOut)
async def get_simulator_status(
    _: User = Depends(_root_only),
    controller: SimulatorController = Depends(get_simulator_controller),
) -> SimulatorStatusOut:
    return _out(controller, await controller.status())


@router.post("/start", response_model=SimulatorStatusOut)
async def start_simulator(
    request: Request,
    body: Optional[SimulatorStartRequest] = None,
    actor: User = Depends(_root_only),
    controller: SimulatorController = Depends(get_simulator_controller),
) -> SimulatorStatusOut:
    body = body or SimulatorStartRequest()
    # Unless SIMULATOR_BACKEND_URL pins one, the task posts back to whatever URL reached us —
    # on AWS that's the Lambda Function URL (CloudFront forwards to it without the viewer's
    # Host header), which avoids a circular reference between the infra stacks.
    backend_url = str(request.base_url).rstrip("/")
    state = await controller.start(
        actor=actor, truck_count=body.truck_count, backend_url=backend_url
    )
    return _out(controller, state)


@router.post("/stop", response_model=SimulatorStatusOut)
async def stop_simulator(
    _: User = Depends(_root_only),
    controller: SimulatorController = Depends(get_simulator_controller),
) -> SimulatorStatusOut:
    return _out(controller, await controller.stop())
