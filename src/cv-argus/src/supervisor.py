"""Which top-level component died, and what exit code that means. Stdlib only, so it can be
tested without TensorFlow (`main.py` imports the model stack).

`main()` used to watch only the pipeline: if the orchestrator or the Bluetooth sender thread
died, the process kept running with the camera on and nothing reaching the ESP32 (CV-10,
SND-10). Exiting non-zero instead lets the container's restart policy bring everything back.
"""
from __future__ import annotations

from typing import Mapping


def _alive(component) -> bool:
    is_alive = component.is_alive
    # Pipeline.is_alive() is a method; the Orchestrator/SenderServer expose a property.
    return bool(is_alive() if callable(is_alive) else is_alive)


def first_dead(components: Mapping[str, object]) -> str | None:
    """The name of the first component that's no longer running. `None` entries are components
    disabled for this run (e.g. `SENDER_TRANSPORT=none`) and are skipped."""
    for name, component in components.items():
        if component is not None and not _alive(component):
            return name
    return None


def exit_code(*, stop_requested: bool, dead: str | None) -> int:
    """0 for a requested stop, or the pipeline ending on its own (a video file reaching EOF, with
    every other component still healthy); 1 when a component died."""
    if stop_requested or dead is None:
        return 0
    return 1
