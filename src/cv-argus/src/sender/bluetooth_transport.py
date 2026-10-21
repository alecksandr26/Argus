"""Classic Bluetooth SPP transport — stdlib `socket.AF_BLUETOOTH`/`BTPROTO_RFCOMM` (Linux with
Bluetooth support compiled in), not PyBluez, avoiding a third-party dependency this project's own
convention would otherwise need to justify (see `requirements.txt`).

`socket.AF_BLUETOOTH` is referenced only inside `__init__`/`accept()` method bodies here, never
at class or module level, so **importing this module is always safe** even on a platform/Python
build without Bluetooth support compiled in (most dev laptops included) — only *constructing* a
`BluetoothSppListener` can fail there, with a clear `AttributeError`/`OSError`, not a silent
no-op. `sender/__init__.py` re-exports both classes lazily (mirroring `pipeline/__init__.py`'s
`_LAZY` pattern) for the same reason: importing `cv_argus.sender` at all must not require
Bluetooth support to be present.
"""

import socket
import struct

from .transport import Listener, Transport

# Linux's RFCOMM socket security option (<bluetooth/bluetooth.h>): `struct bt_security {u8
# level; u8 key_size;}` set at SOL_BLUETOOTH/BT_SECURITY. CPython doesn't export these names,
# hence the literal fallbacks. Set on the listening socket, the kernel copies it to every
# accepted connection and refuses to complete one that doesn't meet it.
SOL_BLUETOOTH = getattr(socket, "SOL_BLUETOOTH", 274)
BT_SECURITY = getattr(socket, "BT_SECURITY", 4)
# `medium` = the link must be paired (bonded) and encrypted. That is what makes it safe for
# `CONFIG` to hand the device key over this link (see device_config.py); `low` lets any device in
# range connect unencrypted -- bench use only. `high` also needs MITM-protected pairing, which an
# ESP32 with no display/keypad can't do (Just Works pairing), so it isn't the default.
SECURITY_LEVELS = {"low": 1, "medium": 2, "high": 3}
DEFAULT_SECURITY = "medium"
# "Any local adapter". CPython parses an RFCOMM address strictly as six hex pairs, so the
# PyBluez-style "" is rejected ("bad bluetooth address") before the kernel ever sees it.
BDADDR_ANY = getattr(socket, "BDADDR_ANY", "00:00:00:00:00:00")


def security_level(name: str) -> int:
    """`BLUETOOTH_SECURITY` env value -> kernel level. Raises `ValueError` on an unknown name."""
    try:
        return SECURITY_LEVELS[name.strip().lower()]
    except KeyError:
        raise ValueError(
            f"Unknown Bluetooth security level {name!r} -- expected one of "
            f"{', '.join(SECURITY_LEVELS)}"
        ) from None


# The longest line a client may send without a newline. Protocol lines are tiny (`PULL 50`, an
# ACK of at most MAX_PULL_LIMIT 32-char ids ~ 1.7 KB); past this the client is broken, and
# buffering further would grow memory without bound (SND-08).
MAX_LINE_BYTES = 4096


class BluetoothSppTransport(Transport):
    def __init__(self, sock: "socket.socket", peer: str | None = None) -> None:
        self._sock = sock
        self._buf = b""
        self.peer = peer

    def readline(self, timeout: float | None = None) -> bytes | None:
        self._sock.settimeout(timeout)
        try:
            while b"\n" not in self._buf:
                chunk = self._sock.recv(4096)
                if not chunk:
                    return None  # clean disconnect
                self._buf += chunk
                if b"\n" not in self._buf and len(self._buf) > MAX_LINE_BYTES:
                    self._buf = b""
                    return None  # a runaway line: drop it and end the session
        except OSError:
            return None  # timeout or a socket error -- treated the same, see Transport docstring
        line, _, rest = self._buf.partition(b"\n")
        self._buf = rest
        return line

    def write(self, data: bytes) -> None:
        self._sock.sendall(data)

    def close(self) -> None:
        try:
            self._sock.close()
        except OSError:
            pass


class BluetoothSppListener(Listener):
    def __init__(self, channel: int = 4, security: str = DEFAULT_SECURITY) -> None:
        level = security_level(security)  # validate before touching any socket
        self._sock = socket.socket(socket.AF_BLUETOOTH, socket.SOCK_STREAM, socket.BTPROTO_RFCOMM)
        self._sock.setsockopt(SOL_BLUETOOTH, BT_SECURITY, struct.pack("BB", level, 0))
        self._sock.bind((BDADDR_ANY, channel))
        self._sock.listen(1)

    def accept(self, timeout: float | None = None) -> Transport | None:
        self._sock.settimeout(timeout)
        try:
            conn, addr = self._sock.accept()
        except OSError:
            return None
        return BluetoothSppTransport(conn, peer=addr[0] if addr else None)

    def close(self) -> None:
        try:
            self._sock.close()
        except OSError:
            pass
