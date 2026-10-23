"""parts_rules: timers, the severity ladder, yawn weighting, head down, recovery, contract mapping.
Driven by a fake 5 fps clock (frame timestamps), so every threshold is checked exactly."""

import pytest

from cv_argus import constants
from cv_argus.orchestrator.parts_rules import (
    ContinuousTimer,
    FrameSignals,
    PartsRuleEngine,
    RuleConfig,
    to_detection,
)

DT = 0.2  # 5 fps


def run(engine, signals, seconds, t0=0.0):
    """Feed the same signals for `seconds` at 5 fps; returns (last state, end time)."""
    t, state = t0, None
    for _ in range(int(round(seconds / DT)) + 1):
        state = engine.update(t, signals)
        t += DT
    return state, t - DT


OPEN = FrameSignals(face=True, eyes_visible=True)
CLOSED = FrameSignals(face=True, eyes_visible=True, eyes_closed=True)


@pytest.mark.parametrize("seconds,severity", [(0.8, "none"), (1.2, "low"), (2.2, "medium"), (3.2, "critical")])
def test_eyes_closed_ladder(seconds, severity):
    st, _ = run(PartsRuleEngine(RuleConfig()), CLOSED, seconds)
    assert st.severity == severity
    if severity != "none":
        assert st.reason == "eyes_closed"


def test_recent_yawn_makes_critical_at_2s():
    e = PartsRuleEngine(RuleConfig())
    _, t = run(e, FrameSignals(face=True, eyes_visible=True, mouth_open=True), 2.0)
    st, t = run(e, OPEN, 1.0, t + DT)              # yawn ended 1 s ago (< 10 s)
    assert st.after_yawn and st.eye_thresholds == (0.7, 1.3, 2.0)
    st, _ = run(e, CLOSED, 2.2, t + DT)
    assert (st.severity, st.reason) == ("critical", "yawn_eyes_closed")


def test_yawn_and_closed_eyes_together():
    st, _ = run(PartsRuleEngine(RuleConfig()),
                FrameSignals(face=True, eyes_visible=True, eyes_closed=True, mouth_open=True), 2.2)
    assert st.severity == "critical"


def test_yawn_weight_expires_after_10s():
    e = PartsRuleEngine(RuleConfig())
    _, t = run(e, FrameSignals(face=True, eyes_visible=True, mouth_open=True), 2.0)
    st, t = run(e, OPEN, 11.0, t + DT)
    assert not st.after_yawn
    st, _ = run(e, CLOSED, 2.2, t + DT)
    assert st.severity == "medium"                 # normal ladder again


def test_head_down_with_eyes_open_is_medium_at_3s():
    st, _ = run(PartsRuleEngine(RuleConfig()), FrameSignals(face=True, eyes_visible=True, head_down=True), 3.2)
    assert (st.severity, st.reason) == ("medium", "head_down")


def test_head_down_with_eyes_hidden_is_critical_at_3s():
    e = PartsRuleEngine(RuleConfig())
    st, _ = run(e, FrameSignals(face=True, eyes_visible=False, head_down=True), 2.6)
    assert st.severity == "none"
    st, _ = run(e, FrameSignals(face=True, eyes_visible=False, head_down=True), 0.6, 2.8)
    assert (st.severity, st.reason) == ("critical", "head_down_eyes_closed")


def test_yawning_alone_never_exceeds_low():
    e = PartsRuleEngine(RuleConfig())
    t, st = 0.0, None
    for _ in range(3):                             # three 2 s yawns
        st, t = run(e, FrameSignals(face=True, eyes_visible=True, mouth_open=True), 2.0, t)
        st, t = run(e, OPEN, 3.0, t + DT)
    assert st.yawn_events == 3 and (st.severity, st.reason) == ("low", "yawning")


def test_one_flickered_frame_does_not_reset_the_timer():
    e = PartsRuleEngine(RuleConfig())
    _, t = run(e, CLOSED, 1.6)
    e.update(t + DT, OPEN)                         # one open frame (0.2 s <= grace 0.4 s)
    st, _ = run(e, CLOSED, 1.6, t + 2 * DT)
    assert st.timers["eyes_closed"] >= 3.0 and st.severity == "critical"


def test_severity_holds_through_recovery():
    e = PartsRuleEngine(RuleConfig())
    _, t = run(e, CLOSED, 3.2)
    st, t = run(e, OPEN, 1.6, t + DT)
    assert st.severity == "critical"               # still held (< 2 s recovery)
    st, _ = run(e, OPEN, 0.8, t + DT)
    assert st.severity == "none"


def test_trigger_keeps_the_real_closure_length_during_the_hold():
    """A reading written while the alarm is held (eyes already open again) must still say how
    long the eyes were closed -- seen in a real run, where the live timer read 0.0."""
    e = PartsRuleEngine(RuleConfig())
    _, t = run(e, CLOSED, 3.2)
    st, _ = run(e, OPEN, 1.0, t + DT)
    assert st.severity == "critical" and st.timers["eyes_closed"] == 0.0
    assert st.trigger_s == pytest.approx(3.2) and to_detection(st).extras["trigger_s"] == pytest.approx(3.2)


NOFACE = FrameSignals(face=False)


@pytest.mark.parametrize("seconds,severity", [(3.2, "none"), (4.8, "none"), (5.0, "critical"),
                                              (10.2, "critical")])
def test_no_face_after_a_face_is_a_single_critical_at_5s(seconds, severity):
    """No ladder (contract C1.5): nothing at all before 5 s, then critical straight away."""
    e = PartsRuleEngine(RuleConfig())
    _, t = run(e, OPEN, 1.0)
    st, _ = run(e, NOFACE, seconds, t + DT)
    assert st.severity == severity
    if severity != "none":
        assert st.reason == "no_face"


def test_no_face_never_passes_through_low_or_medium():
    e = PartsRuleEngine(RuleConfig())
    _, t = run(e, OPEN, 1.0)
    t += DT
    seen = []
    for _ in range(40):                            # 8 s without a face
        seen.append(e.update(t, NOFACE).severity)
        t += DT
    assert set(seen) == {"none", "critical"}
    assert seen.index("critical") == 25            # the frame where the timer reaches 5.0 s


def test_no_face_threshold_is_the_c4_constant():
    assert constants.RULE_NO_FACE_CRITICAL_S == 5.0
    assert not hasattr(constants, "RULE_NO_FACE_S")
    assert not hasattr(constants, "RULE_NO_FACE_MEDIUM_S")


def test_no_face_before_any_face_raises_nothing():
    st, _ = run(PartsRuleEngine(RuleConfig()), NOFACE, 30.0)
    assert st.severity == "none"


def test_an_incident_holds_while_the_face_is_missing():
    # Eyes closed into critical, then the driver slumps out of frame: no recovery, ever, while
    # the face is gone (the eye timer stops, which used to decay the alarm after 2 s).
    e = PartsRuleEngine(RuleConfig())
    _, t = run(e, CLOSED, 3.2)
    st, _ = run(e, NOFACE, 30.0, t + DT)
    assert st.severity == "critical"


def test_recovery_needs_the_face_back_and_clear():
    e = PartsRuleEngine(RuleConfig())
    _, t = run(e, CLOSED, 3.2)
    _, t = run(e, NOFACE, 4.0, t + DT)
    st, t = run(e, OPEN, 1.0, t + DT)
    assert st.severity == "critical"              # 1 s of a clear face: not yet
    st, _ = run(e, OPEN, 2.0, t + DT)
    assert st.severity == "none"                  # 2 s clear with the face visible


def test_thresholds_come_from_env(monkeypatch):
    monkeypatch.setenv("RULE_EYES_CRITICAL_S", "1.6")
    st, _ = run(PartsRuleEngine(RuleConfig()), CLOSED, 1.8)
    assert st.severity == "critical"


def test_contract_mapping():
    for sev, level, drowsy in (("none", 1, False), ("low", 1, False), ("medium", 2, True), ("critical", 2, True)):
        st, _ = run(PartsRuleEngine(RuleConfig()), OPEN, 0.0)
        st.severity = sev
        d = to_detection(st)
        assert d.level == level and d.class_name == ("Drowsy" if drowsy else "Not Drowsy")
        assert (d.probabilities[1] >= 0.57) == drowsy                         # ESP32's threshold
        assert d.probabilities.sum() == pytest.approx(1.0)
        assert d.extras["severity"] == sev and "timers" in d.extras
    assert constants.RULE_P_DROWSY["medium"] >= 0.57 > constants.RULE_P_DROWSY["low"]


def test_only_the_two_best_eye_tracks_count():
    from types import SimpleNamespace as T

    from cv_argus.model.parts_detector import CLOSED_EYE, OPEN_EYE
    from cv_argus.orchestrator.parts_rules import signals_from_tracks
    eye = lambda state, score, visible=True: T(kind="eye", state=state, score=score, visible=visible)
    # two real closed eyes + a weak ghost "open eye" -> still closed
    assert signals_from_tracks(True, [eye(CLOSED_EYE, .9), eye(CLOSED_EYE, .8), eye(OPEN_EYE, .4)]).eyes_closed
    # a stale (not visible) open-eye track loses to the visible closed ones
    assert signals_from_tracks(True, [eye(OPEN_EYE, .95, False), eye(CLOSED_EYE, .7), eye(CLOSED_EYE, .6)]).eyes_closed
    assert not signals_from_tracks(True, [eye(OPEN_EYE, .9), eye(CLOSED_EYE, .8)]).eyes_closed


def test_continuous_timer_grace():
    tm = ContinuousTimer(grace=0.4)
    for k in range(6):                             # true for 1.0 s at 5 fps
        tm.update(k * DT, True)
    assert tm.duration == pytest.approx(1.0)
    assert tm.update(1.2, False) == pytest.approx(1.0)   # gap 0.2 s: held, stops growing
    assert tm.update(1.4, False) == pytest.approx(1.0)   # gap 0.4 s (float 0.4000...04): still held
    assert tm.update(1.6, False) == 0.0                  # gap 0.6 s: reset


def test_one_missed_frame_at_5fps_float_timestamps():
    """Regression: timestamps from ms/1000 make a one-frame gap 0.4000...04 s > 0.4 s."""
    tm = ContinuousTimer(grace=0.4)
    for ms in (1000, 1200, 1400):
        tm.update(ms / 1000, True)
    tm.update(1600 / 1000, False)
    assert tm.update(1800 / 1000, True) == pytest.approx(0.8)


def test_head_timer_survives_alternating_misses():
    """head_down is caught on ~half the frames: two missed frames in a row (0.6 s) must not reset
    the head timer the way they would the eye timer."""
    e = PartsRuleEngine(RuleConfig())
    down = FrameSignals(face=True, eyes_visible=True, head_down=True)
    t, st = 0.0, None
    for k in range(20):                                # 4 s: down, miss, miss, down, ...
        st = e.update(t, down if k % 3 == 0 else OPEN)
        t += DT
    assert st.timers["head_down"] >= 3.0 and st.severity == "medium" and st.reason == "head_down"


# --- reset_signals() (contract C1.7: a face switch resets the timers, never the held severity) ---

def test_reset_signals_keeps_a_held_critical():
    e = PartsRuleEngine(RuleConfig())
    _, t = run(e, CLOSED, 3.2)
    e.reset_signals()
    st = e.update(t + DT, OPEN)
    assert (st.severity, st.reason) == ("critical", "eyes_closed")
    assert st.trigger_s == pytest.approx(3.2)
    assert st.timers == {"eyes_closed": 0.0, "head_down": 0.0, "mouth_open": 0.0, "no_face": 0.0}


def test_reset_signals_new_face_still_needs_the_full_recovery():
    e = PartsRuleEngine(RuleConfig())
    _, t = run(e, CLOSED, 3.2)
    st, t = run(e, OPEN, 1.6, t + DT)             # 1.6 s into the 2 s recovery
    assert st.severity == "critical"
    e.reset_signals()                              # the recovery timer starts over
    st, t = run(e, OPEN, 1.6, t + DT)
    assert st.severity == "critical"
    st, _ = run(e, OPEN, 0.4, t + DT)
    assert st.severity == "none"


def test_reset_signals_keeps_a_no_face_critical_through_the_switch():
    """C1.7 [sync 1]: the no-face critical fires on or before the switch frame, the face stage
    feeds that frame first, then resets; the new face must recover it like any other."""
    e = PartsRuleEngine(RuleConfig())
    _, t = run(e, OPEN, 1.0)
    st, t = run(e, NOFACE, 5.0, t + DT)            # the switch frame (5 s missing)
    assert st.severity == "critical"
    e.reset_signals()
    st, t = run(e, OPEN, 1.8, t + DT)
    assert (st.severity, st.reason) == ("critical", "no_face")
    st, _ = run(e, OPEN, 0.2, t + DT)
    assert st.severity == "none"


def test_reset_signals_clears_running_timers_and_yawns():
    e = PartsRuleEngine(RuleConfig())
    _, t = run(e, FrameSignals(face=True, eyes_visible=True, mouth_open=True), 2.0)
    st, t = run(e, CLOSED, 1.2, t + DT)            # after a yawn: 1.2 s closed -> low
    assert st.after_yawn and st.severity == "low" and st.yawn_events == 1
    e.reset_signals()
    st = e.update(t + DT, CLOSED)
    assert not st.after_yawn and st.yawn_events == 0
    assert st.timers["eyes_closed"] == 0.0
    st, _ = run(e, CLOSED, 1.2, t + 2 * DT)        # counts from the first frame after the reset
    assert st.timers["eyes_closed"] == pytest.approx(1.4)


def test_reset_signals_remembers_a_face_was_seen():
    e = PartsRuleEngine(RuleConfig())
    _, t = run(e, OPEN, 1.0)
    e.reset_signals()
    st, _ = run(e, NOFACE, 5.0, t + DT)
    assert st.severity == "critical"
