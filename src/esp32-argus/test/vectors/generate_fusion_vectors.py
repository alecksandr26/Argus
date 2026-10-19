"""Writes fusion_vectors.json: scripted inputs and what cv-argus's fusion_contract.py (the
reference) decides for each, so every other copy of the fusion state machine can be checked
against the same cases (X-15 in docs/designs/edge-cloud-scenarios.md):

- fusion.cpp, the firmware port: test/host/fusion_vectors_host_test.cpp
- fusion_contract.py itself, which src/tests/edge_chain/esp32_sim.py drives:
  src/tests/edge_chain/test_firmware_vectors.py (also catches a stale JSON)
- the HIL suite's virtual_esp32.py, once that suite is on main

Re-run after changing fusion_contract.py on purpose, and review the diff:

    python src/esp32-argus/test/vectors/generate_fusion_vectors.py

Times are whole or half seconds so a float32 `at` on the ESP32 is exact.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
CV_ARGUS_SRC = HERE.parents[2] / "cv-argus" / "src"
OUT = HERE / "fusion_vectors.json"

CONFIG = {"debounce_seconds": 5.0, "escalation_seconds": 20.0, "recovery_seconds": 10.0}


def _load_contract():
    if "cv_argus" not in sys.modules:
        spec = importlib.util.spec_from_file_location(
            "cv_argus", CV_ARGUS_SRC / "__init__.py", submodule_search_locations=[str(CV_ARGUS_SRC)]
        )
        module = importlib.util.module_from_spec(spec)
        sys.modules["cv_argus"] = module
        spec.loader.exec_module(module)
    from cv_argus.orchestrator import fusion_contract

    return fusion_contract


def _readings(drowsy: bool, grip: str, times):
    return [{"op": "update", "drowsy": drowsy, "grip": grip, "at": float(t)} for t in times]


def _posted(alert_id: str):
    return [{"op": "posted", "id": alert_id}]


# name -> steps. "update" feeds one reading; "posted" is recordAlertPosted(id).
CASES = {
    "medium_after_debounce": _readings(True, "good", [0, 2, 4, 5, 6]),
    "blip_shorter_than_debounce": _readings(True, "good", [0, 2, 4]) + _readings(False, "good", [5, 7, 9]),
    "drowsy_bad_grip_goes_straight_to_critical": _readings(True, "bad", [0, 3, 5]),
    "grip_only_incident": _readings(False, "bad", [0, 2.5, 5]),
    "medium_escalates_to_critical": _readings(True, "good", [0, 5])
    + _posted("m1")
    + _readings(True, "good", [10, 20, 24.5, 25, 26]),
    "recovery_resolves_the_open_incident": _readings(True, "good", [0, 5])
    + _posted("m1")
    + _readings(False, "good", [6, 10, 15.5, 16, 17]),
    "recovery_interrupted_restarts": _readings(True, "good", [0, 5])
    + _posted("m1")
    + _readings(False, "good", [6, 12])
    + _readings(True, "good", [13])
    + _readings(False, "good", [14, 20, 24, 25]),
    "failed_post_is_raised_again": _readings(True, "bad", [0, 5, 6, 7]) + _posted("c1") + _readings(True, "bad", [8]),
    "partial_improvement_keeps_severity": _readings(True, "bad", [0, 5])
    + _posted("c1")
    + _readings(True, "good", [6, 9])
    + _readings(False, "good", [10, 20, 21]),
    "escalation_supersedes_unposted_medium": _readings(True, "good", [0, 5]) + _readings(True, "bad", [6, 11, 12]),
}


def _alert_dict(alert):
    if alert is None:
        return None
    return {
        "severity": alert.severity_level.value,
        "grip": alert.grip_status.value,
        "related": alert.related_alert_id,
    }


def build() -> dict:
    fc = _load_contract()
    config = fc.FusionConfig(**CONFIG)
    cases = []
    for name, steps in CASES.items():
        orch = fc.FusionOrchestrator(config)
        out_steps = []
        for step in steps:
            step = dict(step)
            if step["op"] == "posted":
                orch.record_alert_posted(step["id"])
            else:
                decision = orch.update(
                    fc.FusionReading(
                        drowsy=fc.DrowsyState.DROWSY if step["drowsy"] else fc.DrowsyState.NOT_DROWSY,
                        grip=fc.GripState(step["grip"]),
                        at=step["at"],
                    )
                )
                step["expect"] = {
                    "severity": decision.severity.value,
                    "alert": _alert_dict(decision.alert),
                    "resolved": decision.resolved_incident_id,
                }
            out_steps.append(step)
        cases.append({"name": name, "steps": out_steps})
    return {"generated_by": "generate_fusion_vectors.py", "config": CONFIG, "cases": cases}


def main() -> None:
    OUT.write_text(json.dumps(build(), indent=1) + "\n")
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
