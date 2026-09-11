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

from .transport import Listener, Transport


class BluetoothSppTransport(Transport):
    def __init__(self, sock: "socket.socket") -> None:
        self._sock = sock
        self._buf = b""

    def readline(self, timeout: float | None = None) -> bytes | None:
        self._sock.settimeout(timeout)
        try:
            while b"\n" not in self._buf:
                chunk = self._sock.recv(4096)
                if not chunk:
                    return None  # clean disconnect
                self._buf += chunk
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
    def __init__(self, channel: int = 4) -> None:
        self._sock = socket.socket(socket.AF_BLUETOOTH, socket.SOCK_STREAM, socket.BTPROTO_RFCOMM)
        self._sock.bind(("", channel))
        self._sock.listen(1)

    def accept(self, timeout: float | None = None) -> Transport | None:
        self._sock.settimeout(timeout)
        try:
            conn, _addr = self._sock.accept()
        except OSError:
            return None
        return BluetoothSppTransport(conn)

    def close(self) -> None:
        try:
            self._sock.close()
        except OSError:
            pass
