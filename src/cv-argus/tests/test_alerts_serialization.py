"""`alerts/serialization.py` — round-trip and malformed-input handling."""

import pytest

from cv_argus.alerts import Alert, AlertKind, age_seconds, from_dict, from_json, to_dict, to_json


def test_drowsiness_alert_round_trips_through_dict():
    alert = Alert.new(
        AlertKind.DROWSINESS,
        level=2,
        source_id="cam0",
        payload={"class_name": "Drowsy", "probabilities": [0.2, 0.8]},
    )
    assert from_dict(to_dict(alert)) == alert


def test_no_face_alert_round_trips_through_dict():
    alert = Alert.new(AlertKind.DROWSINESS, level=None, source_id="cam0",
                      payload={"class_name": "No Face", "probabilities": None})
    assert from_dict(to_dict(alert)) == alert


def test_drowsiness_alert_round_trips_through_json():
    alert = Alert.new(
        AlertKind.DROWSINESS,
        level=2,
        source_id="cam0",
        payload={"class_name": "Drowsy", "probabilities": [0.2, 0.8]},
    )
    assert from_json(to_json(alert)) == alert


def test_to_json_has_no_embedded_newline():
    """Required by sender/'s newline-delimited-JSON wire format (see sender/protocol.py) --
    a stray newline inside one line's JSON would corrupt the framing."""
    alert = Alert.new(
        AlertKind.DROWSINESS,
        level=2,
        source_id="cam0",
        payload={"class_name": "Drowsy", "probabilities": [0.2, 0.8]},
    )
    assert "\n" not in to_json(alert)


def _reading():
    return Alert.new(AlertKind.DROWSINESS, level=1, source_id="cam0",
                     payload={"class_name": "Not Drowsy", "probabilities": [0.9, 0.1]})


def test_from_dict_rejects_unknown_kind():
    data = to_dict(_reading())
    data["kind"] = "panic"
    with pytest.raises(ValueError, match="unknown Alert kind"):
        from_dict(data)


def test_from_dict_rejects_the_removed_route_status_kind():
    data = to_dict(_reading())
    data["kind"] = "route_status"
    with pytest.raises(ValueError, match="unknown Alert kind"):
        from_dict(data)


def test_from_json_rejects_unknown_kind():
    bad_json = to_json(_reading()).replace('"drowsiness"', '"panic"')
    with pytest.raises(ValueError, match="unknown Alert kind"):
        from_json(bad_json)


# --- age_s (contract C1.4: computed at serve time, never stored) --------------------------------

def _stamped(mono_ms, boot_id, created_at_ms=1_000_000, clock_synced=True):
    return Alert(id="x", kind=AlertKind.DROWSINESS, level=1, created_at_ms=created_at_ms,
                 source_id="cam0", payload={}, mono_ms=mono_ms, boot_id=boot_id,
                 clock_synced=clock_synced)


def test_age_same_boot_uses_the_monotonic_clock():
    a = _stamped(mono_ms=10_000, boot_id="b1", created_at_ms=0)
    assert age_seconds(a, boot_id="b1", mono_ms_now=11_449, wall_ms_now=999_999_999,
                       synced_now=True) == 1.4


def test_age_other_boot_uses_the_wall_clock_when_both_are_synced():
    a = _stamped(mono_ms=10_000, boot_id="old", created_at_ms=1_000_000)
    assert age_seconds(a, boot_id="b1", mono_ms_now=1, wall_ms_now=1_037_000,
                       synced_now=True) == 37.0


def test_age_wall_clock_is_clamped_at_zero():
    a = _stamped(mono_ms=None, boot_id=None, created_at_ms=2_000_000)
    assert age_seconds(a, boot_id="b1", wall_ms_now=1_000_000, synced_now=True) == 0.0


@pytest.mark.parametrize("row_synced,now_synced", [(False, True), (True, False), (None, True)])
def test_age_is_unknowable_without_a_synced_clock_on_both_sides(row_synced, now_synced):
    a = _stamped(mono_ms=10_000, boot_id="old", clock_synced=row_synced)
    assert age_seconds(a, boot_id="b1", mono_ms_now=20_000, wall_ms_now=2_000_000,
                       synced_now=now_synced) is None


def test_age_defaults_to_this_process():
    a = Alert.new(AlertKind.DROWSINESS, level=1, source_id="cam0", payload={})
    age = age_seconds(a)
    assert age is not None and 0.0 <= age < 5.0


# --- Wire-format corner cases --------------------------------------------------------------------

import json  # noqa: E402

import pytest  # noqa: E402

from cv_argus.alerts import Alert, AlertKind  # noqa: E402
from cv_argus.alerts.serialization import from_dict, from_json, to_dict, to_json  # noqa: E402


def test_round_trip_preserves_probability_precision():
    alert = Alert.new(
        AlertKind.DROWSINESS,
        level=2,
        source_id="cam0",
        payload={"class_name": "Drowsy", "probabilities": [0.123456789, 0.876543211]},
    )
    assert from_json(to_json(alert)).payload["probabilities"] == [0.123456789, 0.876543211]


def test_json_line_has_no_newline_even_with_newlines_in_payload():
    alert = Alert.new(
        AlertKind.DROWSINESS, level=1, source_id="cam\n0", payload={"note": "a\nb"}
    )
    assert "\n" not in to_json(alert)


def test_unknown_kind_raises_clear_error():
    data = to_dict(Alert.new(AlertKind.DROWSINESS, level=1, source_id="c", payload={}))
    data["kind"] = "camera_status"
    with pytest.raises(ValueError, match="camera_status"):
        from_dict(data)


def test_record_written_before_the_clock_fields_existed_still_parses():
    """Rows already sitting in a Pi's buffer from an older release must still deserialize."""
    old = {
        "id": "abc",
        "kind": "drowsiness",
        "level": 2,
        "created_at_ms": 1,
        "source_id": "cam0",
        "payload": {"class_name": "Drowsy", "probabilities": [0.2, 0.8]},
        "geolocation": None,
    }
    assert from_dict(old).id == "abc"


def test_no_face_payload_round_trips():
    alert = Alert.new(
        AlertKind.DROWSINESS,
        level=None,
        source_id="cam0",
        payload={"class_name": "No Face", "probabilities": None, "transition": True},
    )
    back = from_json(to_json(alert))
    assert (back.level, back.payload) == (None, alert.payload)


def test_clock_fields_are_on_the_wire():
    alert = Alert.new(AlertKind.DROWSINESS, level=2, source_id="cam0", payload={})
    wire = json.loads(to_json(alert))
    assert {"mono_ms", "boot_id", "clock_synced"} <= wire.keys()


def _reject_constant(token):
    raise ValueError(f"non-standard JSON token {token}")


def test_wire_json_is_strict_even_with_nan_probabilities():
    """The ESP32's ArduinoJson 7 rejects a whole line containing NaN/Infinity (verified against
    7.4.3: InvalidInput), so the wire format must be strict JSON whatever the payload holds."""
    alert = Alert.new(
        AlertKind.DROWSINESS, level=2, source_id="cam0",
        payload={"class_name": "Drowsy", "probabilities": [float("nan"), float("inf")]},
    )
    json.loads(to_json(alert), parse_constant=_reject_constant)
