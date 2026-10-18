"""Parsing string ids from requests and stored references into Mongo ObjectIds.

Ids arrive from places the API doesn't control: a request body (the ESP32's `ROUTE_ID` from
`config.h`, which can still be a `"CHANGE_ME"` placeholder) or a stored reference
(`Route.id_truck`). `PydanticObjectId(value)` raises on anything that isn't 24 hex chars, which
used to surface as a 500. Everything that turns a string into a document lookup goes through
here instead, so a bad id is just "not found".
"""
from __future__ import annotations

from typing import Optional, TypeVar

from beanie import Document, PydanticObjectId
from fastapi import HTTPException, status

DocT = TypeVar("DocT", bound=Document)


def to_object_id(value: object) -> Optional[PydanticObjectId]:
    if not value:
        return None
    try:
        return PydanticObjectId(value)
    except Exception:
        return None


async def get_by_id(model: type[DocT], value: object) -> Optional[DocT]:
    """The document with this id, or `None` for a missing *or malformed* id."""
    oid = to_object_id(value)
    return await model.get(oid) if oid is not None else None


async def get_or_404(model: type[DocT], value: object, name: str) -> DocT:
    doc = await get_by_id(model, value)
    if doc is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"{name} not found")
    return doc
