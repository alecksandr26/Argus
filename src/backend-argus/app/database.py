"""Motor client + Beanie initialization. Called once from `app.main`'s lifespan handler."""
from __future__ import annotations

from beanie import init_beanie
from motor.motor_asyncio import AsyncIOMotorClient

from app.config import settings
from app.models import DOCUMENT_MODELS


async def init_db(client: AsyncIOMotorClient | None = None) -> AsyncIOMotorClient:
    """Accepts an optional pre-built `client` so tests can pass a mongomock-motor client (or a
    real one pointed at a testcontainers Mongo) instead of always connecting to `settings.mongo_uri`."""
    if client is None:
        client = AsyncIOMotorClient(settings.mongo_uri)
    await init_beanie(database=client[settings.mongo_db], document_models=DOCUMENT_MODELS)
    return client
