"""The one place GeoJSON's lon-first coordinate order is allowed to matter.

Three fields (`Route.destination_coordinates`, `Status_Route.current_coordinates`,
`Alert.coordinates`) are stored internally as MongoDB GeoJSON Points so `2dsphere` indexing and
real geo queries work, but the API always speaks `{lat, lon}` — the exact shape
`ui-argus/src/types.ts`'s `Coordinates` interface already declares, so its existing
`normalizeCoordinates()` adapter needs no changes. Every conversion between the two goes through
`to_geojson`/`to_latlon` below; nothing else in this codebase should construct a `GeoJSONPoint`
or read `.coordinates` directly.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, field_validator


class GeoJSONPoint(BaseModel):
    """Internal storage shape — a MongoDB-native GeoJSON Point, `[lon, lat]` order."""

    type: Literal["Point"] = "Point"
    coordinates: tuple[float, float]

    @field_validator("coordinates")
    @classmethod
    def _validate_range(cls, value: tuple[float, float]) -> tuple[float, float]:
        lon, lat = value
        if not (-180.0 <= lon <= 180.0):
            raise ValueError(f"longitude out of range: {lon}")
        if not (-90.0 <= lat <= 90.0):
            raise ValueError(f"latitude out of range: {lat}")
        return value


class Coordinates(BaseModel):
    """API-facing shape — matches `ui-argus/src/types.ts`'s `Coordinates` exactly."""

    lat: float
    lon: float


def to_geojson(coordinates: Coordinates) -> GeoJSONPoint:
    return GeoJSONPoint(coordinates=(coordinates.lon, coordinates.lat))


def to_latlon(point: GeoJSONPoint) -> Coordinates:
    lon, lat = point.coordinates
    return Coordinates(lat=lat, lon=lon)
