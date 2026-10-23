"""Motor client + Beanie initialization. Called once from `app.main`'s lifespan handler."""
from __future__ import annotations

from beanie import init_beanie
from motor.motor_asyncio import AsyncIOMotorClient

from app.config import settings
from app.indexes import ensure_indexes
from app.models import DOCUMENT_MODELS


async def init_db(client: AsyncIOMotorClient | None = None) -> AsyncIOMotorClient:
    """Accepts an optional pre-built `client` so tests can pass a mongomock-motor client (or a
    real one pointed at a testcontainers Mongo) instead of always connecting to `settings.mongo_uri`."""
    if client is None:
        # tz_aware: datetimes come back UTC-aware instead of naive (BE-24).
        client = AsyncIOMotorClient(settings.mongo_uri, tz_aware=True)
    await init_beanie(database=client[settings.mongo_db], document_models=DOCUMENT_MODELS)
    await ensure_indexes(client[settings.mongo_db], strict=False)
    return client
