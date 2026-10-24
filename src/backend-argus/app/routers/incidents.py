"""Incidents: grouped alerts of one route (docs/designs/final-tweaks-contracts.md, C3.4/C3.6).
Reading is any logged-in user; closing is guardian + root_admin only."""
from __future__ import annotations

from typing import Literal, Optional

from fastapi import APIRouter, Depends, Query

from app.auth.dependencies import get_current_user, require_role
from app.ids import get_or_404
from app.incidents import close_incident, incidents_out
from app.models.common import Role
from app.models.incident import Incident
from app.models.user import User
from app.schemas.incident import IncidentClose, IncidentOut

router = APIRouter(prefix="/api/incidents", tags=["incidents"])


@router.get("", response_model=list[IncidentOut], dependencies=[Depends(get_current_user)])
async def list_incidents(
    route_id: Optional[str] = Query(default=None),
    status_filter: Literal["open", "closed", "all"] = Query(default="all", alias="status"),
    limit: int = Query(default=100, ge=1, le=500),
) -> list[IncidentOut]:
    """Newest `last_alert_at` first."""
    query: dict = {}
    if route_id is not None:
        query["id_route"] = route_id
    if status_filter == "open":
        query["closed_at"] = None
    elif status_filter == "closed":
        query["closed_at"] = {"$ne": None}
    incidents = await Incident.find(query).sort(-Incident.last_alert_at).limit(limit).to_list()
    return await incidents_out(incidents, feed=False)


@router.get(
    "/{incident_id}", response_model=IncidentOut, dependencies=[Depends(get_current_user)]
)
async def get_incident(incident_id: str) -> IncidentOut:
    incident = await get_or_404(Incident, incident_id, "Incident")
    return (await incidents_out([incident], feed=False))[0]


@router.post("/{incident_id}/close", response_model=IncidentOut)
async def close(
    incident_id: str,
    body: IncidentClose,
    user: User = Depends(require_role(Role.GUARDIAN, Role.ROOT_ADMIN)),
) -> IncidentOut:
    """Closes the incident and every alert in it, with a note (required for a critical one).
    The route's `incident_state` drops to the worst of its remaining open incidents. Only a
    guardian or root_admin closes; the driver's own "OK" never does."""
    incident = await get_or_404(Incident, incident_id, "Incident")
    closed = await close_incident(incident, str(user.id), body.note)
    return (await incidents_out([closed], feed=False))[0]
