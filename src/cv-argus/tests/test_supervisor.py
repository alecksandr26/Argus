"""`cv_argus.supervisor` — the stdlib-only check `main()` uses to notice a dead component.

`main()` used to only watch `pipeline.is_alive()`: if the orchestrator thread or the Bluetooth
sender died, the process kept running with the camera on and nothing reaching the ESP32 (CV-10,
SND-10). Exiting non-zero lets the container's restart policy bring everything back.
`main.py` itself imports TensorFlow, so the logic lives here where it can be tested without it.
"""
from __future__ import annotations

import pytest

_BUG = "CV-10/SND-10: main() only watches the pipeline"


class _Component:
    def __init__(self, alive):
        self.is_alive = alive


@pytest.mark.xfail(strict=True, reason=_BUG)
def test_first_dead_names_the_dead_component():
    from cv_argus.supervisor import first_dead

    components = {
        "pipeline": _Component(True),
        "orchestrator": _Component(False),
        "sender": _Component(True),
    }
    assert first_dead(components) == "orchestrator"


@pytest.mark.xfail(strict=True, reason=_BUG)
def test_first_dead_is_none_when_all_alive_and_ignores_disabled_ones():
    from cv_argus.supervisor import first_dead

    components = {"pipeline": _Component(True), "orchestrator": _Component(True), "sender": None}
    assert first_dead(components) is None


@pytest.mark.xfail(strict=True, reason=_BUG)
def test_pipeline_is_alive_as_a_method_is_supported():
    """`Pipeline.is_alive()` is a method; the threads expose a property. Both must work."""
    from cv_argus.supervisor import first_dead

    class _Pipeline:
        def is_alive(self):
            return False

    assert first_dead({"pipeline": _Pipeline(), "orchestrator": _Component(True)}) == "pipeline"


@pytest.mark.xfail(strict=True, reason=_BUG)
def test_exit_code_is_zero_on_requested_stop_and_one_when_a_component_died():
    from cv_argus.supervisor import exit_code

    assert exit_code(stop_requested=True, dead=None) == 0
    assert exit_code(stop_requested=False, dead=None) == 0  # e.g. a video file reached EOF
    assert exit_code(stop_requested=False, dead="sender") == 1
