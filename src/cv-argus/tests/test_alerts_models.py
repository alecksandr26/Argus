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


# --- Clock fields for fusion timing and ordering (CV-13) ----------------------------------------
# The ESP32 fuses readings by *when they happened on the Pi*, not when it pulled them. Wall
# clock (`created_at_ms`) can jump (NTP) or be wrong (no network, no RTC battery), so each record
# also needs a monotonic stamp, the boot it belongs to (monotonic resets on reboot), and whether
# the wall clock was synced.

import pytest  # noqa: E402

from cv_argus.alerts import route_status  # noqa: E402



def test_alert_carries_mono_ms_boot_id_and_clock_synced():
    alert = Alert.new(AlertKind.DROWSINESS, level=2, source_id="cam0", payload={})
    assert isinstance(alert.mono_ms, int) and alert.mono_ms > 0
    assert isinstance(alert.boot_id, str) and alert.boot_id
    assert isinstance(alert.clock_synced, bool)


def test_mono_ms_is_monotonic_across_alerts():
    a = Alert.new(AlertKind.DROWSINESS, level=1, source_id="cam0", payload={})
    b = route_status("cam0")
    assert b.mono_ms >= a.mono_ms


def test_boot_id_is_stable_within_a_process():
    a = Alert.new(AlertKind.DROWSINESS, level=1, source_id="cam0", payload={})
    b = Alert.new(AlertKind.DROWSINESS, level=1, source_id="cam0", payload={})
    assert a.boot_id == b.boot_id
