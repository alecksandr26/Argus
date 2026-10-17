"""`alerts/serialization.py` — round-trip and malformed-input handling."""

import pytest

from cv_argus.alerts import Alert, AlertKind, from_dict, from_json, route_status, to_dict, to_json


def test_drowsiness_alert_round_trips_through_dict():
    alert = Alert.new(
        AlertKind.DROWSINESS,
        level=2,
        source_id="cam0",
        payload={"class_name": "Drowsy", "probabilities": [0.2, 0.8]},
    )
    assert from_dict(to_dict(alert)) == alert


def test_route_status_alert_round_trips_through_dict():
    alert = route_status("cam0")
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


def test_from_dict_rejects_unknown_kind():
    data = to_dict(route_status("cam0"))
    data["kind"] = "panic"
    with pytest.raises(ValueError, match="unknown Alert kind"):
        from_dict(data)


def test_from_json_rejects_unknown_kind():
    alert = route_status("cam0")
    bad_json = to_json(alert).replace('"route_status"', '"panic"')
    with pytest.raises(ValueError, match="unknown Alert kind"):
        from_json(bad_json)


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


@pytest.mark.xfail(strict=True, reason="CV-13: clock fields aren't on the wire")
def test_clock_fields_are_on_the_wire():
    alert = Alert.new(AlertKind.DROWSINESS, level=2, source_id="cam0", payload={})
    wire = json.loads(to_json(alert))
    assert {"mono_ms", "boot_id", "clock_synced"} <= wire.keys()
