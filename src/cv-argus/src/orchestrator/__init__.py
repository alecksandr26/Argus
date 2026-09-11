"""`orchestrator/` — decision logic: given a `DetectionResult` (and, later, other signals like
the grip sensor), decides whether it's worth raising an Alert, and emits a periodic
RouteStatus("OK") heartbeat otherwise. Builds records via `alerts/` and hands them to `buffer/`.
See `orchestrator.py`'s module docstring for the decision-loop design.
"""

from .bridge import OrchestratorBridgeOutputStage
from .orchestrator import Orchestrator

__all__ = ["Orchestrator", "OrchestratorBridgeOutputStage"]
