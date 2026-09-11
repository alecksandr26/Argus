"""`alerts/models.py` — `Alert.new()` and `route_status()`."""

from cv_argus.alerts import Alert, AlertKind, route_status


def test_new_sets_a_uuid4_hex_id():
    alert = Alert.new(AlertKind.DROWSINESS, level=2, source_id="cam0", payload={})
    assert isinstance(alert.id, str)
    assert len(alert.id) == 32
    int(alert.id, 16)  # raises ValueError if it isn't hex


def test_new_geolocation_is_always_none():
    alert = Alert.new(AlertKind.DROWSINESS, level=2, source_id="cam0", payload={})
    assert alert.geolocation is None


def test_new_stamps_wall_clock_created_at_ms():
    import time

    before = int(time.time() * 1000)
    alert = Alert.new(AlertKind.DROWSINESS, level=2, source_id="cam0", payload={})
    after = int(time.time() * 1000)
    assert before <= alert.created_at_ms <= after


def test_two_new_alerts_get_different_ids():
    a = Alert.new(AlertKind.DROWSINESS, level=2, source_id="cam0", payload={})
    b = Alert.new(AlertKind.DROWSINESS, level=2, source_id="cam0", payload={})
    assert a.id != b.id


def test_route_status_defaults_to_ok():
    alert = route_status("cam0")
    assert alert.kind == AlertKind.ROUTE_STATUS
    assert alert.level is None
    assert alert.payload == {"status": "OK"}
    assert alert.geolocation is None


def test_route_status_accepts_a_custom_status():
    alert = route_status("cam0", status="DEGRADED")
    assert alert.payload == {"status": "DEGRADED"}
