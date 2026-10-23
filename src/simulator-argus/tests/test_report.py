"""The combined report body (C2.1) and how each answer is read (C2.2/C2.3)."""
from __future__ import annotations

import pytest

from simulator_argus.report import (
    GpsPoint,
    PointBuffer,
    build_report,
    classify_response,
    classify_transport_error,
)


def _point(t: float, vigilance: str = "low", **kw) -> GpsPoint:
    return GpsPoint(t=t, lat=20.0 + t / 1000, lon=-103.0, speed=80.0, vigilance=vigilance, **kw)


def _ok_body(event=None, status="ok", active_route="r1"):
    return {
        "points": {"stored": 1, "route": active_route, "status": status},
        "event": event,
        "active_route": active_route,
    }


# --- body ---------------------------------------------------------------------------------


def test_point_buffer_keeps_only_the_newest_10():
    buffer = PointBuffer()
    for t in range(15):
        buffer.add(_point(float(t)))
    points = buffer.drain()
    assert [p.t for p in points] == [float(t) for t in range(5, 15)]
    assert len(buffer) == 0


def test_build_report_orders_points_oldest_first_with_relative_ages():
    body = build_report([_point(103.0), _point(100.0)], None, now=104.0)
    assert [p["age_s"] for p in body["points"]] == [4.0, 1.0]
    assert "event" not in body
    assert set(body["points"][0]) == {
        "age_s",
        "lat",
        "lon",
        "speed",
        "vigilance",
        "last_known",
        "location_age_s",
    }


def test_build_report_includes_the_event():
    event = {"client_event_id": "x"}
    body = build_report([], event, now=1.0)
    assert body == {"points": [], "event": event}


def test_a_last_known_point_reports_speed_0_and_its_fix_age():
    wire = _point(10.0, last_known=True, location_age_s=42.04).to_wire(now=10.0)
    assert wire["last_known"] is True
    assert wire["speed"] == 0.0
    assert wire["location_age_s"] == 42.0


# --- answers ------------------------------------------------------------------------------


@pytest.mark.parametrize("status", ["created", "duplicate"])
def test_created_or_duplicate_is_delivered(status):
    outcome = classify_response(200, _ok_body({"status": status, "reason": None, "id_alert": "a"}), True)
    assert outcome.event_result == "delivered"
    assert outcome.route_known and outcome.active_route == "r1"
    assert not outcome.route_finished


def test_rejected_carries_the_reason():
    outcome = classify_response(
        200, _ok_body({"status": "rejected", "reason": "too_old", "id_alert": None}), True
    )
    assert (outcome.event_result, outcome.event_reason) == ("rejected", "too_old")


def test_route_finished_points_end_the_trip():
    by_points = classify_response(200, _ok_body(status="route_finished", active_route=None), False)
    assert by_points.route_finished
    assert by_points.route_known and by_points.active_route is None


def test_an_event_rejected_route_finished_ends_the_trip_only_with_no_active_route():
    rejected = {"status": "rejected", "reason": "route_finished", "id_alert": None}

    no_route = classify_response(
        200, _ok_body(rejected, status="no_active_route", active_route=None), True
    )
    assert no_route.route_finished

    # A route is active (e.g. a dead-letter replay from an earlier, ended route): only that
    # event is over, not the trip.
    still_driving = classify_response(200, _ok_body(rejected, status="ok", active_route="r2"), True)
    assert (still_driving.event_result, still_driving.event_reason) == (
        "rejected",
        "route_finished",
    )
    assert not still_driving.route_finished


def test_no_active_route_is_known_null_but_not_finished():
    outcome = classify_response(200, _ok_body(status="no_active_route", active_route=None), False)
    assert outcome.route_known and outcome.active_route is None
    assert not outcome.route_finished
    assert outcome.event_result is None


def test_a_200_that_does_not_answer_for_the_event_is_a_failed_attempt():
    outcome = classify_response(200, _ok_body(None), True)
    assert (outcome.event_result, outcome.event_reason) == ("failed", "bad_response")


def test_401_is_a_failed_attempt_and_asks_for_reconfiguration():
    outcome = classify_response(401, {"detail": "x"}, True)
    assert outcome.reconfigure
    assert (outcome.event_result, outcome.event_reason) == ("failed", "http_401")
    assert not outcome.route_known


@pytest.mark.parametrize("status_code", [413, 422])
def test_a_malformed_report_rejects_the_event_as_invalid(status_code):
    outcome = classify_response(status_code, {"detail": "x"}, True)
    assert (outcome.event_result, outcome.event_reason) == ("rejected", "invalid")


@pytest.mark.parametrize("status_code", [429, 500, 503])
def test_429_and_5xx_are_failed_attempts(status_code):
    outcome = classify_response(status_code, None, True)
    assert (outcome.event_result, outcome.event_reason) == ("failed", f"http_{status_code}")
    assert not outcome.route_known


def test_a_points_only_failure_has_no_event_result():
    outcome = classify_response(503, None, False)
    assert outcome.event_result is None and outcome.http_status == 503


def test_transport_errors_are_failed_attempts():
    outcome = classify_transport_error("timeout", True)
    assert (outcome.http_status, outcome.event_result, outcome.event_reason) == (
        None,
        "failed",
        "timeout",
    )
