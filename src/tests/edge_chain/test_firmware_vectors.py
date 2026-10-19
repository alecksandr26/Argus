"""X-15: the shared fusion vectors (src/esp32-argus/test/vectors/fusion_vectors.json) against the
Python fusion contract that esp32_sim.py drives. fusion_vectors_host_test.cpp replays the same
file against the firmware's fusion.cpp, so any change one copy gets and the other doesn't fails
one of the two suites, instead of the copies drifting apart silently.
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

VECTORS_DIR = Path(__file__).resolve().parents[2] / "esp32-argus" / "test" / "vectors"
VECTORS = json.loads((VECTORS_DIR / "fusion_vectors.json").read_text())


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
