"""`orchestrator/orchestrator.py` — the records that become backend events (contract C1.5), driven
directly (not through threads/queues) for determinism, against a fake `Buffer` that just records
`enqueue()` calls. The reading policy (class changes, keepalives, No Face, warm-up) is in
test_orchestrator_readings.py.

The heartbeat these tests used to cover is gone (contract C1.5): `test_no_heartbeat_*` below
checks it stays gone. What replaced it is "one alert per incident": a medium raise, one critical
escalation, one recovery (driver OK); a reason change at the same severity sends nothing.
"""

import pytest

from cv_argus import constants
from cv_argus.alerts import AlertKind
from cv_argus.orchestrator import Orchestrator

_RANK = {"none": 0, "low": 1, "medium": 2, "critical": 3}


class _FakeBuffer:
    def __init__(self):
        self.enqueued = []

    def enqueue(self, alert):
        self.enqueued.append(alert)


class _Probs(list):
    def tolist(self):
        return list(self)


class _Det:
    def __init__(self, severity, reason):
        p = constants.RULE_P_DROWSY[severity]
        self.level = 2 if severity in ("medium", "critical") else 1
        self.probabilities = _Probs([round(1 - p, 4), p])
        self.extras = {"severity": severity, "reason": reason, "model": "parts-rules-v1"}


def S(severity, reason=None):
    return _Det(severity, reason or ("ok" if severity == "none" else "eyes_closed"))


@pytest.fixture
def clock(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr("time.monotonic", lambda: now[0])
    return now


def _orchestrator(**overrides):
    buf = _FakeBuffer()
    kwargs = dict(debounce_frames=3, warmup_frames=0, keepalive_seconds=2.0)
    kwargs.update(overrides)
    return Orchestrator(buf, **kwargs), buf


def _feed(orch, clock, dets, source="cam0"):
    for det in dets:
        orch._on_detection(source, det)
        clock[0] += 0.2


def _events(buf):
    """Backend events by contract C1.5's rule (what the ESP32 computes)."""
    out = []
    for r in buf.enqueued:
        p = r.payload
        if not p.get("transition") or "severity" not in p:
            continue
        sev, prev = p["severity"], p.get("previous_severity") or "none"
        if sev == "medium" and _RANK[prev] < 2:
            out.append(("alert", "medium", p["reason"], p.get("episode")))
        elif sev == "critical" and _RANK[prev] < 3:
            out.append(("alert", "critical", p["reason"], p.get("episode")))
        elif _RANK[sev] <= 1 and _RANK[prev] >= 2:
            out.append(("ok", None, p["reason"], p.get("episode")))
    return out


def test_no_heartbeat_on_poll_timeouts(clock):
    orch, buf = _orchestrator()
    for _ in range(200):                  # 200 s of poll timeouts with no camera at all
        clock[0] += 1.0
        orch._tick("cam0")
    assert buf.enqueued == []
    assert not hasattr(orch, "_maybe_heartbeat")
    assert not hasattr(constants, "ORCHESTRATOR_HEARTBEAT_INTERVAL_SECONDS")


def test_heartbeat_argument_is_gone():
    with pytest.raises(TypeError):
        Orchestrator(_FakeBuffer(), heartbeat_interval_seconds=60.0)


def test_one_incident_is_medium_then_one_critical_then_one_ok(clock):
    orch, buf = _orchestrator(keepalive_seconds=999.0)
    _feed(orch, clock, [S("none")] * 3 + [S("low")] * 3 + [S("medium")] * 5
          + [S("critical")] * 10 + [S("none")] * 5)
    events = _events(buf)
    assert [(k, s) for k, s, _, _ in events] == [("alert", "medium"), ("alert", "critical"), ("ok", None)]
    episodes = {e for _, _, _, e in events}
    assert len(episodes) == 1 and None not in episodes
    assert len(next(iter(episodes))) == 12


def test_a_reason_change_at_the_same_severity_sends_nothing(clock):
    orch, buf = _orchestrator(keepalive_seconds=999.0)
    _feed(orch, clock, [S("none")] * 3 + [S("critical", "eyes_closed")] * 3)
    before = len(buf.enqueued)
    _feed(orch, clock, [S("critical", "head_down_eyes_closed")] * 5 + [S("critical", "no_face")] * 5)
    assert len(buf.enqueued) == before


def test_critical_to_medium_is_a_record_but_not_an_event(clock):
    orch, buf = _orchestrator(keepalive_seconds=999.0)
    _feed(orch, clock, [S("none")] * 3 + [S("critical")] * 3 + [S("medium", "head_down")] * 3)
    last = buf.enqueued[-1].payload
    assert (last["previous_severity"], last["severity"], last["transition"]) == ("critical", "medium", True)
    assert [(k, s) for k, s, _, _ in _events(buf)] == [("alert", "critical")]


def test_no_face_goes_straight_to_one_critical(clock):
    """With the ladder gone the rules give none -> critical for a missing face; that is one
    critical alert, never a low or medium step first."""
    orch, buf = _orchestrator(keepalive_seconds=999.0)
    _feed(orch, clock, [S("none")] * 3 + [None] * 25 + [S("critical", "no_face")] * 10)
    assert [(k, s, r) for k, s, r, _ in _events(buf)] == [("alert", "critical", "no_face")]
    assert {r.payload["severity"] for r in buf.enqueued} == {"none", "critical"}


def test_the_episode_ends_with_the_recovery_and_a_new_incident_gets_a_new_one(clock):
    orch, buf = _orchestrator(keepalive_seconds=999.0)
    _feed(orch, clock, [S("none")] * 3 + [S("medium")] * 3 + [S("none")] * 3
          + [S("critical")] * 3 + [S("none")] * 3)
    events = _events(buf)
    assert [(k, s) for k, s, _, _ in events] == [("alert", "medium"), ("ok", None),
                                                 ("alert", "critical"), ("ok", None)]
    assert events[0][3] == events[1][3] != events[2][3] == events[3][3]
    # After the recovery, records carry no episode until the next medium/critical.
    later = [r for r in buf.enqueued if r.payload.get("severity") == "none" and not r.payload["transition"]]
    assert all("episode" not in r.payload for r in later)


def test_keepalives_never_count_as_events(clock):
    orch, buf = _orchestrator()
    _feed(orch, clock, [S("none")] * 3 + [S("critical")] * 60)   # 12 s critical: keepalives
    keepalives = [r for r in buf.enqueued if r.payload["transition"] is False]
    assert len(keepalives) >= 4
    assert all(r.payload["previous_severity"] == r.payload["severity"] == "critical" for r in keepalives)
    assert [(k, s) for k, s, _, _ in _events(buf)] == [("alert", "critical")]


def test_every_record_is_a_drowsiness_record(clock):
    orch, buf = _orchestrator()
    _feed(orch, clock, [S("none")] * 3 + [S("medium")] * 20 + [S("critical", "no_face")] * 20
          + [S("none")] * 20)
    for _ in range(70):
        clock[0] += 1.0
        orch._tick("cam0")
    assert buf.enqueued and {r.kind for r in buf.enqueued} == {AlertKind.DROWSINESS}
