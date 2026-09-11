"""`sender/` — owns communication with the ESP32: a Bluetooth SPP server (the Pi listens, the
ESP32 initiates each poll), answering a small custom PULL/ACK protocol (see `protocol.py`).
`BluetoothSppTransport`/`BluetoothSppListener` (`bluetooth_transport.py`) are the real,
platform-gated transport; `FakeTransport`/`FakeListener` (`transport.py`) are in-memory
stand-ins for hermetic tests.

**Lazy submodule import, deliberately** — `BluetoothSppTransport`/`BluetoothSppListener` are
importable as `cv_argus.sender.<Name>`, but only resolved on first attribute access (PEP 562),
mirroring `pipeline/__init__.py`'s identical pattern. See `bluetooth_transport.py`'s module
docstring for why: importing `cv_argus.sender` at all must not require Bluetooth support to
actually be present on the current platform.
"""

import importlib

from .protocol import (
    DEFAULT_PULL_LIMIT,
    END_LINE,
    format_ack,
    format_acked,
    format_pull,
    parse_ack,
    parse_pull,
)
from .server import SenderServer
from .transport import FakeListener, FakeTransport, Listener, Transport

__all__ = [
    "Transport",
    "Listener",
    "FakeTransport",
    "FakeListener",
    "BluetoothSppTransport",
    "BluetoothSppListener",
    "SenderServer",
    "DEFAULT_PULL_LIMIT",
    "END_LINE",
    "parse_pull",
    "format_pull",
    "parse_ack",
    "format_ack",
    "format_acked",
]

_LAZY = {
    "BluetoothSppTransport": (".bluetooth_transport", "BluetoothSppTransport"),
    "BluetoothSppListener": (".bluetooth_transport", "BluetoothSppListener"),
}


def __getattr__(name: str):
    try:
        module_name, attr_name = _LAZY[name]
    except KeyError:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from None
    module = importlib.import_module(module_name, __name__)
    value = getattr(module, attr_name)
    globals()[name] = value  # cache -- subsequent access skips __getattr__ entirely
    return value
