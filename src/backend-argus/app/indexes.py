"""The unique and TTL indexes, created outside Beanie's `Settings.indexes`.

Why separately: Beanie creates its indexes on every start and raises on a conflict, which would
stop the whole backend from booting. These three can conflict with data already in a database
(duplicates that predate the unique index) or with the plain index the same key had before, so:
- `init_db` calls `ensure_indexes(strict=False)`: it tries each one and logs a warning on
  failure, and the app keeps its application-level dedup checks;
- `scripts/migrate_final_tweaks.py` drops the old plain indexes, removes duplicates, and then
  calls `ensure_indexes(strict=True)`, which raises on any failure.

Contracts: docs/designs/final-tweaks-contracts.md C2.2 (dedup) and C3.3 (retention).
"""
from __future__ import annotations

import logging

import pymongo
from pymongo.errors import PyMongoError

from app.constants import GPS_FULL_RETENTION

logger = logging.getLogger(__name__)

ALERT_EVENT_UNIQUE = "uniq_route_client_event"
DRIVER_OK_EVENT_UNIQUE = "uniq_route_client_event"
POINT_TIME_UNIQUE = "uniq_route_timestamp"
POINT_TTL = "ttl_timestamp_not_kept"

_STRING_EVENT_ID = {"client_event_id": {"$type": "string"}}


def index_specs() -> dict[str, list[pymongo.IndexModel]]:
    """Collection name -> the indexes this module owns there."""
    return {
        "alerts": [
            pymongo.IndexModel(
                [("id_route", pymongo.ASCENDING), ("client_event_id", pymongo.ASCENDING)],
                name=ALERT_EVENT_UNIQUE,
                unique=True,
                partialFilterExpression=_STRING_EVENT_ID,
            ),
        ],
        "driver_oks": [
            pymongo.IndexModel(
                [("id_route", pymongo.ASCENDING), ("client_event_id", pymongo.ASCENDING)],
                name=DRIVER_OK_EVENT_UNIQUE,
                unique=True,
            ),
        ],
        "status_routes": [
            pymongo.IndexModel(
                [("id_route", pymongo.ASCENDING), ("timestamp", pymongo.ASCENDING)],
                name=POINT_TIME_UNIQUE,
                unique=True,
            ),
            # A TTL index must be single-field. Points with keep=True (one per route-minute) and
            # old points without the field never expire.
            pymongo.IndexModel(
                [("timestamp", pymongo.ASCENDING)],
                name=POINT_TTL,
                expireAfterSeconds=int(GPS_FULL_RETENTION.total_seconds()),
                partialFilterExpression={"keep": False},
            ),
        ],
    }


async def ensure_indexes(database, *, strict: bool) -> list[str]:
    """Creates the indexes of `index_specs()` on `database` (a Motor database). Returns the
    names that failed (always empty when `strict`, which raises instead)."""
    failed: list[str] = []
    for collection, specs in index_specs().items():
        for spec in specs:
            # create_index(keys, **options), not create_indexes([spec]): same result on a real
            # server, and mongomock (the test tier) drops partialFilterExpression on the latter.
            document = dict(spec.document)
            keys = list(document.pop("key").items())
            try:
                await database[collection].create_index(keys, **document)
            except PyMongoError as exc:
                if strict:
                    raise
                name = spec.document["name"]
                failed.append(f"{collection}.{name}")
                logger.warning(
                    "Could not create index %s.%s (%s); run scripts/migrate_final_tweaks.py",
                    collection,
                    name,
                    exc,
                )
    return failed
