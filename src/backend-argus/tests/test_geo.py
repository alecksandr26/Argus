"""Pure unit tests for app/geo.py — no DB needed, matching the "pure function, no I/O" pattern
src/cv-argus uses for its own math-only helpers."""
import pytest

from app.geo import Coordinates, GeoJSONPoint, to_geojson, to_latlon


def test_to_geojson_orders_lon_first():
    point = to_geojson(Coordinates(lat=19.4326, lon=-99.1332))
    assert point.type == "Point"
    assert point.coordinates == (-99.1332, 19.4326)


def test_to_latlon_round_trips():
    original = Coordinates(lat=20.6597, lon=-103.3496)
    assert to_latlon(to_geojson(original)) == original


@pytest.mark.parametrize("lon,lat", [(200.0, 0.0), (0.0, 95.0), (-181.0, 0.0)])
def test_geojson_point_rejects_out_of_range(lon, lat):
    with pytest.raises(ValueError):
        GeoJSONPoint(coordinates=(lon, lat))
