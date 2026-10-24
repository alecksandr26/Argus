"""`orchestrator/` — decision logic: turns each `DetectionResult` into readings for the ESP32
(class/severity transitions plus 2 s keepalives; no heartbeat). Builds records via
`alerts/` and hands them to `buffer/`. See `orchestrator.py`'s module docstring for the
decision-loop design.

The grip sensor is **not** fused in here — per the root `CLAUDE.md` and `src/esp32-argus/
README.md`, grip lives on the ESP32, which is also the only place both the camera signal and the
grip signal are simultaneously available. `fusion_contract.py` in this same package is the typed
contract + reference decision logic for *that* fusion — a spec/reference module, not wired into
this package's running `Orchestrator` or `main.py`.
"""

from .bridge import OrchestratorBridgeOutputStage
from .orchestrator import Orchestrator

__all__ = ["Orchestrator", "OrchestratorBridgeOutputStage"]
