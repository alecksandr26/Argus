"""The vectors shared with the firmware's host tests (src/esp32-argus/test/vectors/), so a rule
one copy changes and the other doesn't fails one of the suites instead of drifting silently:

- event_vectors.json (C1.5, which Pi records are backend events) against esp32_sim.classify();
  event_vectors_host_test.cpp replays it against pi_event_policy.cpp, and cv-argus's buffer
  tests against the Pi's own copy of the rule;
- X-15: fusion_vectors.json against the Python fusion contract; fusion_vectors_host_test.cpp
  replays it against fusion.cpp (unused while there's no grip sensor, kept in sync anyway).
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

from cv_argus.orchestrator.fusion_contract import (
    DrowsyState,
    FusionConfig,
    FusionOrchestrator,
    FusionReading,
    GripState,
)

from .esp32_sim import classify, classify_record

VECTORS_DIR = Path(__file__).resolve().parents[2] / "esp32-argus" / "test" / "vectors"
VECTORS = json.loads((VECTORS_DIR / "fusion_vectors.json").read_text())
EVENT_VECTORS = json.loads((VECTORS_DIR / "event_vectors.json").read_text())


@pytest.mark.parametrize("case", EVENT_VECTORS["cases"],
                         ids=[c["name"] for c in EVENT_VECTORS["cases"]])
def test_event_rule_matches_the_shared_vectors(case):
    assert classify(case["transition"], case["severity"], case["previous_severity"]) == \
        case["expect"]
    payload = {"transition": case["transition"], "severity": case["severity"]}
    if case["previous_severity"] is not None:
        payload["previous_severity"] = case["previous_severity"]
    assert classify_record({"payload": payload}) == case["expect"]


def test_event_vectors_cover_every_outcome():
    assert {c["expect"] for c in EVENT_VECTORS["cases"]} == {
        "none", "alert_medium", "alert_critical", "ok"}


def _generator():
    spec = importlib.util.spec_from_file_location(
        "generate_fusion_vectors", VECTORS_DIR / "generate_fusion_vectors.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_vectors_file_is_what_the_generator_writes_today():
    """A stale JSON would let both suites agree on outdated expectations: regenerate it
    (generate_fusion_vectors.py) when fusion_contract.py changes on purpose."""
    assert _generator().build() == VECTORS


@pytest.mark.parametrize("case", VECTORS["cases"], ids=[c["name"] for c in VECTORS["cases"]])
def test_fusion_contract_matches_the_shared_vectors(case):
    orch = FusionOrchestrator(FusionConfig(**VECTORS["config"]))
    for i, step in enumerate(case["steps"], start=1):
        if step["op"] == "posted":
            orch.record_alert_posted(step["id"])
            continue
        decision = orch.update(
            FusionReading(
                drowsy=DrowsyState.DROWSY if step["drowsy"] else DrowsyState.NOT_DROWSY,
                grip=GripState(step["grip"]),
                at=step["at"],
            )
        )
        alert = decision.alert
        got = {
            "severity": decision.severity.value,
            "alert": None
            if alert is None
            else {
                "severity": alert.severity_level.value,
                "grip": alert.grip_status.value,
                "related": alert.related_alert_id,
            },
            "resolved": decision.resolved_incident_id,
        }
        assert got == step["expect"], f"step {i} at={step['at']}"
