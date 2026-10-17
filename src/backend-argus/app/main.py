from __future__ import annotations

from contextlib import asynccontextmanager

import math

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware

from app.auth.bootstrap import ensure_root_admin
from app.config import settings
from app.database import init_db
from app.routers import alerts, auth, devices, drivers, routes, status_routes, trucks, users


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.mongo_client = await init_db()
    await ensure_root_admin()
    if settings.seed_demo_data:
        # Imported lazily so a normal (non-demo) boot never pays for scripts/'s import cost.
        # `reset=False` — additive/idempotent, see app/config.py's `seed_demo_data` docstring.
        from scripts.seed_dev_data import seed as seed_demo_data

        await seed_demo_data(
            reset=False,
            client=app.state.mongo_client,
            credentials_file=settings.seed_credentials_file or None,
        )
    yield
    app.state.mongo_client.close()


app = FastAPI(
    title="Argus backend",
    description=(
        "FastAPI + MongoDB backend for Argus (Driver Monitoring System). Covers "
        "User/Truck/Driver/Route/Status_Route/Alert + auth — see src/backend-argus/CLAUDE.md "
        "for the full design rationale and known scope limits."
    ),
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

def _json_safe(value):
    """NaN/Infinity can't be encoded as JSON; render them as strings."""
    if isinstance(value, float) and not math.isfinite(value):
        return str(value)
    if isinstance(value, dict):
        return {k: _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    return value


@app.exception_handler(RequestValidationError)
async def _validation_error(_request: Request, exc: RequestValidationError) -> JSONResponse:
    """FastAPI's default 422 echoes each bad input back. A NaN/Infinity coordinate (which the
    schemas reject) then can't be serialized and the 422 turns into a 500 (BE-03)."""
    return JSONResponse(status_code=422, content={"detail": _json_safe(jsonable_encoder(exc.errors()))})


app.include_router(auth.router)
app.include_router(users.router)
app.include_router(trucks.router)
app.include_router(drivers.router)
app.include_router(routes.router)
app.include_router(status_routes.router)
app.include_router(alerts.router)
app.include_router(devices.router)


@app.get("/health", tags=["health"])
async def health() -> dict:
    return {"status": "ok"}
