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
