"""Opt-in tier: a real MongoDB (via testcontainers) for the handful of things
`mongomock-motor` can't faithfully emulate — real `2dsphere` index behavior in particular.
Deselected from a plain `pytest` run (see pyproject.toml's `addopts`), and additionally
skipped unless Docker is actually usable, matching src/cv-argus's `@pytest.mark.docker` gating
convention (env var + docker-on-PATH check) rather than failing a CI run that never opted in.

Run explicitly with: `ARGUS_BACKEND_MONGO_TESTS=1 pytest -m mongo`
"""
from __future__ import annotations

import os
import shutil

import pytest
import pytest_asyncio
from motor.motor_asyncio import AsyncIOMotorClient

from app.database import init_db
from app.geo import Coordinates, to_geojson
from app.models.route import Route

pytestmark = pytest.mark.mongo

_ENABLED = os.environ.get("ARGUS_BACKEND_MONGO_TESTS") == "1" and shutil.which("docker")


@pytest_asyncio.fixture
async def real_mongo():
    if not _ENABLED:
        pytest.skip(
            "opt-in: set ARGUS_BACKEND_MONGO_TESTS=1 with docker on PATH to run this tier"
        )
    from testcontainers.mongodb import MongoDbContainer

    with MongoDbContainer("mongo:7") as mongo:
        client = AsyncIOMotorClient(mongo.get_connection_url())
        await init_db(client=client)
        yield client
        client.close()


@pytest.mark.asyncio
async def test_geosphere_index_supports_near_query(real_mongo):
    route = Route(
        id_driver="d1",
        id_truck="t1",
        origin_name="A",
        destination_name="B",
        destination_coordinates=to_geojson(Coordinates(lat=19.4326, lon=-99.1332)),
        estimated_departure="2030-01-01T00:00:00Z",
    )
    await route.insert()

    collection = Route.get_motor_collection()
    near_result = await collection.find_one(
        {
            "destination_coordinates": {
                "$near": {
                    "$geometry": {"type": "Point", "coordinates": [-99.13, 19.43]},
                    "$maxDistance": 5000,
                }
            }
        }
    )
    assert near_result is not None
    assert near_result["_id"] == route.id
