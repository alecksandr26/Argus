"""The Pi side of a virtual truck: C1.5's event rule and the episode lifecycle."""
from __future__ import annotations

import itertools
import re

import pytest

from simulator_argus.events import (
    EpisodeTracker,
    Reading,
    classify_transition,
    vigilance_for,
)

OK = Reading("low", "ok")
MEDIUM = Reading("medium", "eyes_closed", drowsy_score=0.6)
CRITICAL = Reading("critical", "eyes_closed", drowsy_score=0.9)
NO_FACE = Reading("critical", "no_face", drowsy_score=None)


def _tracker() -> EpisodeTracker:
    ids = (f"evt{i}" for i in itertools.count())
    episodes = (f"ep{i:010d}" for i in itertools.count())
    return EpisodeTracker(event_id_factory=lambda: next(ids), episode_factory=lambda: next(episodes))


@pytest.mark.parametrize(
    ("prev", "sev", "expected"),
    [
        ("none", "medium", ("alert", "medium")),
        ("low", "medium", ("alert", "medium")),
        (None, "medium", ("alert", "medium")),
        ("none", "critical", ("alert", "critical")),
        ("medium", "critical", ("alert", "critical")),
        ("medium", "low", ("ok", None)),
        ("critical", "none", ("ok", None)),
        ("critical", "medium", None),  # a de-escalation is a record, never an event
        ("none", "low", None),
        ("low", "none", None),
        ("medium", "medium", None),
        ("critical", "critical", None),
    ],
)
def test_classify_transition_matches_c1_5(prev, sev, expected):
    assert classify_transition(prev, sev) == expected


@pytest.mark.parametrize(
    ("severity", "vigilance"),
    [("none", "low"), ("low", "low"), ("medium", "medium"), ("critical", "critical"), (None, "unknown")],
)
def test_vigilance_for_maps_pi_severity_to_point_vigilance(severity, vigilance):
    assert vigilance_for(severity) == vigilance


def test_an_incident_is_one_medium_one_critical_upgrade_and_one_ok_under_one_episode():
    tracker = _tracker()
    readings = [OK, OK, MEDIUM, MEDIUM, CRITICAL, CRITICAL, OK, OK]
    events = [tracker.update(r, now=float(i)) for i, r in enumerate(readings)]
    fired = [e for e in events if e is not None]

    assert [(e.kind, e.severity) for e in fired] == [
        ("alert", "medium"),
        ("alert", "critical"),
        ("ok", None),
    ]
    assert len({e.episode for e in fired}) == 1
    assert [e.alert_type for e in fired] == ["eyes_closed", "eyes_closed", None]
    assert [e.occurred_at for e in fired] == [2.0, 4.0, 6.0]
    assert len({e.client_event_id for e in fired}) == 3
    assert tracker.episode is None  # closed by the OK


def test_a_reason_change_at_the_same_level_sends_nothing():
    tracker = _tracker()
    assert tracker.update(MEDIUM, 0.0) is not None
    assert tracker.update(Reading("medium", "head_down"), 1.0) is None


def test_critical_back_to_medium_is_not_an_event_and_keeps_the_episode():
    tracker = _tracker()
    first = tracker.update(CRITICAL, 0.0)
    assert tracker.update(MEDIUM, 1.0) is None
    assert tracker.episode == first.episode
    # A second rise to critical in the same episode is another critical upgrade (C1.5).
    again = tracker.update(CRITICAL, 2.0)
    assert again.severity == "critical"
    assert again.episode == first.episode
    ok = tracker.update(OK, 3.0)
    assert ok.kind == "ok" and ok.episode == first.episode


def test_no_face_goes_straight_to_one_critical_without_a_medium_step():
    tracker = _tracker()
    fired = [tracker.update(r, float(i)) for i, r in enumerate([OK, NO_FACE, NO_FACE, OK])]
    fired = [e for e in fired if e is not None]
    assert [(e.kind, e.severity, e.alert_type) for e in fired] == [
        ("alert", "critical", "no_face"),
        ("ok", None, None),
    ]
    assert fired[0].ai_metadata == {"model": "simulator", "scores": None}


def test_each_incident_gets_a_new_episode():
    tracker = _tracker()
    first = tracker.update(MEDIUM, 0.0)
    tracker.update(OK, 1.0)
    second = tracker.update(MEDIUM, 2.0)
    assert first.episode != second.episode


def test_default_ids_have_the_pi_shapes():
    tracker = EpisodeTracker()
    event = tracker.update(MEDIUM, 0.0)
    assert re.fullmatch(r"[0-9a-f]{12}", event.episode)
    assert re.fullmatch(r"[0-9a-f]{32}", event.client_event_id)


def test_unknown_severity_is_refused():
    with pytest.raises(ValueError):
        _tracker().update(Reading("severe", "x"), 0.0)


def test_alert_wire_shape_is_the_c2_event_object():
    tracker = _tracker()
    position = {"lat": 20.0, "lon": -103.0, "last_known": False, "location_age_s": 0}
    event = tracker.update(MEDIUM, now=100.0, position=position, speed=72.4)

    wire = event.to_wire(now=101.66)
    assert wire == {
        "client_event_id": "evt0",
        "episode": "ep0000000000",
        "kind": "alert",
        "severity": "medium",
        "alert_type": "eyes_closed",
        "age_s": 1.7,
        "delivery": "live",
        "queued_seconds": 0,
        "position": position,
        "speed": 72.4,
        "ai_metadata": {"model": "simulator", "scores": {"not_drowsy": 0.4, "drowsy": 0.6}},
    }


def test_ok_wire_shape_has_no_severity_type_or_scores():
    tracker = _tracker()
    tracker.update(MEDIUM, 0.0)
    ok = tracker.update(OK, 5.0, position=None, speed=None)
    wire = ok.to_wire(now=5.0)
    assert wire["kind"] == "ok"
    assert wire["severity"] is None
    assert wire["alert_type"] is None
    assert wire["ai_metadata"] is None
    assert wire["position"] is None  # "position unknown" is allowed on any event
    assert wire["age_s"] == 0.0


def test_dlq_wire_carries_delivery_and_queued_seconds():
    event = _tracker().update(CRITICAL, 0.0)
    wire = event.to_wire(now=90.0, delivery="dlq", queued_seconds=30.04)
    assert wire["delivery"] == "dlq"
    assert wire["queued_seconds"] == 30.0
    assert wire["age_s"] == 90.0
