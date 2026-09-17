from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.auth.bootstrap import ensure_root_admin
from app.config import settings
from app.database import init_db
from app.routers import alerts, auth, drivers, routes, status_routes, trucks, users


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

app.include_router(auth.router)
app.include_router(users.router)
app.include_router(trucks.router)
app.include_router(drivers.router)
app.include_router(routes.router)
app.include_router(status_routes.router)
app.include_router(alerts.router)


@app.get("/health", tags=["health"])
async def health() -> dict:
    return {"status": "ok"}
