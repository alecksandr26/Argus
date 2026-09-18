"""`orchestrator/fusion_contract.py`'s `FusionOrchestrator` — the ESP32's drowsy+grip decision
loop reference implementation. Unlike `Orchestrator`, `update()` takes its timestamp from the
`FusionReading` the caller supplies rather than reading a real clock, so there's no thread, no
real waiting, and no `time.monotonic` monkeypatching needed here — every test just constructs
readings with chosen `at` values landing exactly on/around the configured thresholds.
"""

import pytest

from cv_argus.orchestrator.fusion_contract import (
    AlertToRaise,
    DrowsyState,
    FusionConfig,
    FusionOrchestrator,
    FusionReading,
    GripState,
    Severity,
)

# Small, fast-arithmetic windows distinct from each other and from the real defaults, so a test
# accidentally using the wrong constant would fail loudly rather than coincidentally pass.
_CONFIG = FusionConfig(debounce_seconds=2.0, escalation_seconds=10.0, recovery_seconds=4.0)


def _reading(drowsy: DrowsyState, grip: GripState, at: float) -> FusionReading:
    return FusionReading(drowsy=drowsy, grip=grip, at=at)


def _good(at: float) -> FusionReading:
    return _reading(DrowsyState.NOT_DROWSY, GripState.GOOD, at)


def _medium_via_grip(at: float) -> FusionReading:
    """Not drowsy but bad grip -> medium (the "hand off the wheel" cause)."""
    return _reading(DrowsyState.NOT_DROWSY, GripState.BAD, at)


def _medium_via_drowsy(at: float) -> FusionReading:
    """Drowsy but good grip -> medium (the "fatigued but still gripping" cause)."""
    return _reading(DrowsyState.DROWSY, GripState.GOOD, at)


def _critical(at: float) -> FusionReading:
    return _reading(DrowsyState.DROWSY, GripState.BAD, at)


def _orch() -> FusionOrchestrator:
    return FusionOrchestrator(_CONFIG)


def test_a_short_blip_does_not_confirm_a_new_incident():
    orch = _orch()
    decision = orch.update(_medium_via_grip(at=0.0))
    assert decision.alert is None
    assert decision.severity == Severity.LOW

    # Recovers before debounce_seconds (2.0) elapses -- never confirmed.
    decision = orch.update(_good(at=1.0))
    assert decision.alert is None
    assert decision.severity == Severity.LOW


def test_a_sustained_bad_reading_confirms_at_exactly_debounce_seconds():
    orch = _orch()
    assert orch.update(_medium_via_grip(at=0.0)).alert is None
    assert orch.update(_medium_via_grip(at=1.9)).alert is None  # still below 2.0

    decision = orch.update(_medium_via_grip(at=2.0))  # exactly at the threshold
    assert decision.severity == Severity.MEDIUM
    assert decision.alert == AlertToRaise(
        severity_level=Severity.MEDIUM, grip_status=GripState.BAD, related_alert_id=None
    )


def test_fresh_incident_alert_has_no_related_alert_id():
    orch = _orch()
    orch.update(_medium_via_drowsy(at=0.0))
    decision = orch.update(_medium_via_drowsy(at=2.0))
    assert decision.alert is not None
    assert decision.alert.related_alert_id is None


def test_a_direct_jump_from_clear_to_critical_debounces_once_not_via_medium_first():
    orch = _orch()
    seen_severities = []
    for t in (0.0, 1.0, 2.0):
        decision = orch.update(_critical(at=t))
        seen_severities.append(decision.severity)

    # Never reports MEDIUM at any point -- the instantaneous reading was CRITICAL the whole time.
    assert Severity.MEDIUM not in seen_severities
    assert seen_severities == [Severity.LOW, Severity.LOW, Severity.CRITICAL]


def test_changing_candidate_mid_debounce_restarts_the_timer():
    orch = _orch()
    orch.update(_medium_via_grip(at=0.0))  # candidate: MEDIUM, since 0.0
    orch.update(_medium_via_grip(at=1.0))  # still MEDIUM, 1.0s in -- not yet confirmed

    # Switches to a different worse candidate (CRITICAL) before MEDIUM's debounce completes.
    decision = orch.update(_critical(at=1.5))
    assert decision.alert is None  # restarted, only 0s into the new CRITICAL candidate

    # 1.9s after the restart (at=1.5) -- still short of debounce_seconds (2.0).
    assert orch.update(_critical(at=3.4)).alert is None

    # 2.0s after the restart -- now confirms.
    decision = orch.update(_critical(at=3.5))
    assert decision.severity == Severity.CRITICAL
    assert decision.alert is not None


def test_sudden_worsening_from_medium_to_critical_confirms_after_debounce_not_escalation():
    orch = _orch()
    orch.update(_medium_via_drowsy(at=0.0))
    orch.update(_medium_via_drowsy(at=2.0))  # confirms MEDIUM
    orch.record_alert_posted("medium-id")

    # Both signals suddenly bad -- should confirm CRITICAL after debounce_seconds (2.0), well
    # before escalation_seconds (10.0) would have elapsed.
    orch.update(_critical(at=3.0))
    decision = orch.update(_critical(at=5.0))  # 2.0s after the worsening started at t=3.0
    assert decision.severity == Severity.CRITICAL
    assert decision.alert == AlertToRaise(
        severity_level=Severity.CRITICAL, grip_status=GripState.BAD, related_alert_id="medium-id"
    )
    # Confirmed well before the slow escalation timer (10.0s from t=2.0 -> t=12.0) would fire.
    assert 5.0 < 2.0 + _CONFIG.escalation_seconds


def test_escalation_before_escalation_seconds_elapsed_is_a_no_op():
    orch = _orch()
    orch.update(_medium_via_drowsy(at=0.0))
    orch.update(_medium_via_drowsy(at=2.0))  # confirms MEDIUM at t=2.0

    decision = orch.update(_medium_via_drowsy(at=2.0 + _CONFIG.escalation_seconds - 0.1))
    assert decision.severity == Severity.MEDIUM
    assert decision.alert is None


def test_escalation_at_exactly_escalation_seconds_raises_critical_with_related_alert_id():
    orch = _orch()
    orch.update(_medium_via_drowsy(at=0.0))
    orch.update(_medium_via_drowsy(at=2.0))  # confirms MEDIUM at t=2.0
    orch.record_alert_posted("root-alert-id")

    decision = orch.update(_medium_via_drowsy(at=2.0 + _CONFIG.escalation_seconds))
    assert decision.severity == Severity.CRITICAL
    assert decision.alert == AlertToRaise(
        severity_level=Severity.CRITICAL, grip_status=GripState.GOOD, related_alert_id="root-alert-id"
    )


def test_escalation_without_record_alert_posted_yields_related_alert_id_none():
    orch = _orch()
    orch.update(_medium_via_drowsy(at=0.0))
    orch.update(_medium_via_drowsy(at=2.0))  # confirms MEDIUM -- record_alert_posted never called

    decision = orch.update(_medium_via_drowsy(at=2.0 + _CONFIG.escalation_seconds))
    assert decision.severity == Severity.CRITICAL
    assert decision.alert is not None
    assert decision.alert.related_alert_id is None


def test_partial_improvement_from_critical_to_medium_does_not_deescalate():
    orch = _orch()
    orch.update(_critical(at=0.0))
    orch.update(_critical(at=2.0))  # confirms CRITICAL

    decision = orch.update(_medium_via_drowsy(at=3.0))
    assert decision.severity == Severity.CRITICAL
    assert decision.alert is None


def test_partial_improvement_resets_the_recovery_clock():
    orch = _orch()
    orch.update(_critical(at=0.0))
    orch.update(_critical(at=2.0))  # confirms CRITICAL

    orch.update(_good(at=3.0))  # fully good -- recovery clock starts at t=3.0
    orch.update(_medium_via_drowsy(at=4.0))  # partial improvement only -- clock reset

    # 3.9s after the *second* good run started (t=4.0 doesn't count as good) -- still short of
    # recovery_seconds (4.0) counted from the next fully-good reading.
    orch.update(_good(at=5.0))  # recovery clock restarts here
    decision = orch.update(_good(at=5.0 + _CONFIG.recovery_seconds - 0.1))
    assert decision.severity == Severity.CRITICAL  # not yet resolved
    assert decision.resolved_incident_id is None


def test_recovery_before_recovery_seconds_elapsed_does_not_resolve():
    orch = _orch()
    orch.update(_medium_via_grip(at=0.0))
    orch.update(_medium_via_grip(at=2.0))  # confirms MEDIUM

    orch.update(_good(at=3.0))  # recovery clock starts
    decision = orch.update(_good(at=3.0 + _CONFIG.recovery_seconds - 0.1))
    assert decision.severity == Severity.MEDIUM
    assert decision.resolved_incident_id is None


def test_recovery_at_exactly_recovery_seconds_resolves_and_clears_state():
    orch = _orch()
    orch.update(_medium_via_grip(at=0.0))
    orch.update(_medium_via_grip(at=2.0))  # confirms MEDIUM
    orch.record_alert_posted("root-alert-id")

    orch.update(_good(at=3.0))  # recovery clock starts
    decision = orch.update(_good(at=3.0 + _CONFIG.recovery_seconds))
    assert decision.severity == Severity.LOW
    assert decision.alert is None
    assert decision.resolved_incident_id == "root-alert-id"

    # State fully cleared -- a subsequent bad reading is a brand-new incident.
    orch.update(_medium_via_grip(at=100.0))
    fresh = orch.update(_medium_via_grip(at=100.0 + _CONFIG.debounce_seconds))
    assert fresh.alert is not None
    assert fresh.alert.related_alert_id is None


def test_recovery_clock_resets_if_a_signal_goes_bad_again_mid_recovery():
    orch = _orch()
    orch.update(_medium_via_grip(at=0.0))
    orch.update(_medium_via_grip(at=2.0))  # confirms MEDIUM
    orch.record_alert_posted("root-alert-id")

    orch.update(_good(at=3.0))  # first good run starts
    orch.update(_medium_via_grip(at=4.0))  # goes bad again before recovery_seconds (4.0) elapses

    # 3.9s into the *second* good run (started at t=5.0) -- not yet resolved.
    orch.update(_good(at=5.0))
    decision = orch.update(_good(at=5.0 + _CONFIG.recovery_seconds - 0.1))
    assert decision.severity == Severity.MEDIUM
    assert decision.resolved_incident_id is None

    # But the second good run, held the full window, does resolve.
    decision = orch.update(_good(at=5.0 + _CONFIG.recovery_seconds))
    assert decision.severity == Severity.LOW
    assert decision.resolved_incident_id == "root-alert-id"


def test_record_alert_posted_is_a_no_op_with_no_open_incident():
    orch = _orch()
    orch.record_alert_posted("stray-id")  # nothing open -- should be silently ignored

    orch.update(_medium_via_grip(at=0.0))
    decision = orch.update(_medium_via_grip(at=_CONFIG.debounce_seconds))
    assert decision.alert is not None
    assert decision.alert.related_alert_id is None  # not "stray-id"


def test_record_alert_posted_is_a_no_op_after_the_incident_already_resolved():
    orch = _orch()
    orch.update(_medium_via_grip(at=0.0))
    orch.update(_medium_via_grip(at=_CONFIG.debounce_seconds))
    orch.record_alert_posted("root-alert-id")

    t = _CONFIG.debounce_seconds
    orch.update(_good(at=t + 1.0))
    orch.update(_good(at=t + 1.0 + _CONFIG.recovery_seconds))  # resolves, clears state

    orch.record_alert_posted("late-arriving-id")  # nothing open -- ignored

    orch.update(_medium_via_grip(at=1000.0))
    fresh = orch.update(_medium_via_grip(at=1000.0 + _CONFIG.debounce_seconds))
    assert fresh.alert is not None
    assert fresh.alert.related_alert_id is None  # not "late-arriving-id"


def test_escalations_own_id_becomes_the_resolved_incident_id():
    orch = _orch()
    orch.update(_medium_via_drowsy(at=0.0))
    orch.update(_medium_via_drowsy(at=2.0))  # confirms MEDIUM
    orch.record_alert_posted("root-alert-id")

    escalated = orch.update(_medium_via_drowsy(at=2.0 + _CONFIG.escalation_seconds))
    assert escalated.alert is not None
    orch.record_alert_posted("escalation-alert-id")  # the caller just posted the escalation

    start_recovery = 2.0 + _CONFIG.escalation_seconds
    orch.update(_good(at=start_recovery + 1.0))
    resolved = orch.update(_good(at=start_recovery + 1.0 + _CONFIG.recovery_seconds))
    assert resolved.resolved_incident_id == "escalation-alert-id"  # not "root-alert-id"


def test_repeated_readings_at_steady_state_are_no_ops():
    orch = _orch()
    orch.update(_medium_via_grip(at=0.0))
    orch.update(_medium_via_grip(at=2.0))  # confirms MEDIUM

    for t in (3.0, 5.0, 7.0, 9.0):  # all below escalation_seconds from t=2.0 (-> 12.0)
        decision = orch.update(_medium_via_grip(at=t))
        assert decision.alert is None
        assert decision.severity == Severity.MEDIUM


def test_full_lifecycle_low_to_medium_to_critical_to_recovered_to_low():
    orch = _orch()
    decisions = []

    decisions.append(orch.update(_good(at=0.0)))  # steady low
    decisions.append(orch.update(_medium_via_drowsy(at=1.0)))  # candidate starts
    decisions.append(orch.update(_medium_via_drowsy(at=3.0)))  # confirms medium (2.0s later)
    root_id = "root-alert-id"
    orch.record_alert_posted(root_id)

    decisions.append(orch.update(_medium_via_drowsy(at=6.0)))  # steady medium, no-op
    decisions.append(orch.update(_medium_via_drowsy(at=13.0)))  # 10.0s since t=3.0 -> escalates
    escalation_id = "escalation-alert-id"
    orch.record_alert_posted(escalation_id)

    decisions.append(orch.update(_good(at=14.0)))  # recovery clock starts
    decisions.append(orch.update(_good(at=18.0)))  # 4.0s later -> resolves

    decisions.append(orch.update(_medium_via_grip(at=100.0)))  # a fresh incident starts clean

    severities = [d.severity for d in decisions]
    assert severities == [
        Severity.LOW,
        Severity.LOW,  # still debouncing
        Severity.MEDIUM,  # confirmed
        Severity.MEDIUM,  # steady
        Severity.CRITICAL,  # escalated
        Severity.CRITICAL,  # still recovering
        Severity.LOW,  # resolved
        Severity.LOW,  # fresh candidate, not yet confirmed
    ]

    assert decisions[2].alert == AlertToRaise(
        severity_level=Severity.MEDIUM, grip_status=GripState.GOOD, related_alert_id=None
    )
    assert decisions[4].alert == AlertToRaise(
        severity_level=Severity.CRITICAL, grip_status=GripState.GOOD, related_alert_id=root_id
    )
    assert decisions[6].resolved_incident_id == escalation_id
    assert decisions[7].alert is None  # the fresh incident hasn't debounced in yet
