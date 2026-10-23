"""Endpoints a device calls about itself, identified by its API key alone."""
from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from pydantic import ValidationError

from app.auth.dependencies import get_device_truck
from app.constants import REPORT_MAX_BODY_BYTES
from app.models.truck import Truck
from app.report import active_route_for, process_report
from app.schemas.report import ReportEnvelope, ReportResponse
from app.schemas.route import RouteOut
from app.serializers import route_out

router = APIRouter(prefix="/api/devices", tags=["devices"])


@router.get("/me/active-route", response_model=RouteOut)
async def my_active_route(truck: Truck = Depends(get_device_truck)) -> RouteOut:
    """The route this device's truck is currently on. Kept for the old firmware; a report's
    response carries `active_route` too, so the new ESP32 no longer needs this. If several are
    in progress (a data-entry mistake), the newest departure wins."""
    route = await active_route_for(str(truck.id))
    if route is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No active route for this truck")
    return route_out(route)


@router.post("/me/report", response_model=ReportResponse)
async def report(request: Request, truck: Truck = Depends(get_device_truck)) -> ReportResponse:
    """The one device write: GPS points since the last report plus at most one event
    (docs/designs/final-tweaks-contracts.md, C2). Device key only. 401 bad key, 413 body over
    16 KiB, 422 bad envelope; otherwise 200 with a result per part, even when a part was
    rejected (`event.status: rejected`, `points.status: route_finished`, ...)."""
    declared = request.headers.get("content-length")
    if declared is not None and declared.isdigit() and int(declared) > REPORT_MAX_BODY_BYTES:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "Report body too large")
    raw = await request.body()
    if len(raw) > REPORT_MAX_BODY_BYTES:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "Report body too large")
    try:
        data = json.loads(raw)
    except ValueError:
        raise RequestValidationError(
            [{"type": "json_invalid", "loc": ("body",), "msg": "Body is not valid JSON", "input": None}]
        )
    if not isinstance(data, dict):
        raise RequestValidationError(
            [{"type": "dict_type", "loc": ("body",), "msg": "Body must be a JSON object", "input": None}]
        )
    try:
        envelope = ReportEnvelope.model_validate(data)
    except ValidationError as exc:
        raise RequestValidationError(
            [{**e, "loc": ("body", *e["loc"])} for e in exc.errors(include_url=False)]
        )
    return await process_report(truck, envelope)
