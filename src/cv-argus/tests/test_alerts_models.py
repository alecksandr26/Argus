"""`alerts/models.py` — `Alert.new()`. (`route_status()` and the heartbeat kind were removed in
protocol v2, contract C1.4.)"""

import cv_argus.alerts as alerts_pkg
from cv_argus.alerts import Alert, AlertKind


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


def test_drowsiness_is_the_only_kind():
    """Contract C1.4: `kind` is always "drowsiness"; the route_status heartbeat is gone."""
    assert [k.value for k in AlertKind] == ["drowsiness"]
    assert not hasattr(alerts_pkg, "route_status")


# --- Clock fields for fusion timing and ordering (CV-13) ----------------------------------------
# The ESP32 fuses readings by *when they happened on the Pi*, not when it pulled them. Wall
# clock (`created_at_ms`) can jump (NTP) or be wrong (no network, no RTC battery), so each record
# also needs a monotonic stamp, the boot it belongs to (monotonic resets on reboot), and whether
# the wall clock was synced.

def test_alert_carries_mono_ms_boot_id_and_clock_synced():
    alert = Alert.new(AlertKind.DROWSINESS, level=2, source_id="cam0", payload={})
    assert isinstance(alert.mono_ms, int) and alert.mono_ms > 0
    assert isinstance(alert.boot_id, str) and alert.boot_id
    assert isinstance(alert.clock_synced, bool)


def test_mono_ms_is_monotonic_across_alerts():
    a = Alert.new(AlertKind.DROWSINESS, level=1, source_id="cam0", payload={})
    b = Alert.new(AlertKind.DROWSINESS, level=None, source_id="cam0", payload={})
    assert b.mono_ms >= a.mono_ms


def test_boot_id_is_stable_within_a_process():
    a = Alert.new(AlertKind.DROWSINESS, level=1, source_id="cam0", payload={})
    b = Alert.new(AlertKind.DROWSINESS, level=1, source_id="cam0", payload={})
    assert a.boot_id == b.boot_id
