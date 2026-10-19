"""Device credentials over the Bluetooth link: `DeviceConfig`, the `CONFIG` grammar, the server's
`CONFIG`/allowlist handling (over fakes), and the listener's required security level."""

import pytest

from cv_argus.buffer import open_buffer
from cv_argus.sender import (
    DeviceConfig,
    FakeListener,
    FakeTransport,
    SenderServer,
    bluetooth_transport,
    protocol,
)

ENV = {
    "ARGUS_TRUCK_ID": "64b0a0000000000000000001",
    "ARGUS_DEVICE_API_KEY": "secret-key",
    "ARGUS_BACKEND_URL": "https://demo.example.com/",
}
CONFIG = DeviceConfig.from_env(ENV)


class TestDeviceConfig:
    def test_from_env_full(self):
        assert CONFIG == DeviceConfig(
            truck_id="64b0a0000000000000000001",
            device_api_key="secret-key",
            backend_url="https://demo.example.com",
        )

    def test_from_env_empty_is_none(self):
        assert DeviceConfig.from_env({}) is None
        assert DeviceConfig.from_env({name: "  " for name in ENV}) is None

    def test_from_env_partial_raises_naming_what_is_missing(self):
        with pytest.raises(ValueError, match="ARGUS_BACKEND_URL"):
            DeviceConfig.from_env({**ENV, "ARGUS_BACKEND_URL": ""})

    def test_repr_masks_the_key(self):
        assert "secret-key" not in repr(CONFIG)


class TestConfigGrammar:
    def test_request(self):
        assert protocol.parse_config_request("CONFIG")
        assert protocol.parse_config_request("CONFIG\r")
        assert not protocol.parse_config_request("config")
        assert not protocol.parse_config_request("PULL 5")

    def test_round_trip_is_one_line(self):
        line = protocol.format_config(CONFIG.to_wire())
        assert "\n" not in line
        assert protocol.parse_config(line) == CONFIG.to_wire()

    @pytest.mark.parametrize("line", ["NOCONFIG", "CONFIG", "CONFIG {bad", "CONFIG [1]", "END"])
    def test_parse_rejects(self, line):
        assert protocol.parse_config(line) is None


def _session(server_kwargs, request, peer=None, tmp_path=None):
    """Runs one session against a real SenderServer; returns every line the server sent."""
    buf = open_buffer(tmp_path / "b.sqlite3")
    listener = FakeListener()
    server = SenderServer(buf, listener, accept_timeout=0.1, **server_kwargs)
    server.start()
    try:
        client, server_side = FakeTransport.pair()
        server_side.peer = peer
        listener.offer(server_side)
        client.write((request + "\n").encode())
        lines = []
        while (line := client.readline(timeout=2.0)) is not None:
            lines.append(line.decode())
        return lines
    finally:
        server.stop()
        server.join(timeout=2.0)


class TestServerConfig:
    def test_config_is_served(self, tmp_path):
        lines = _session({"device_config": CONFIG}, "CONFIG", tmp_path=tmp_path)
        assert len(lines) == 1
        assert protocol.parse_config(lines[0]) == CONFIG.to_wire()

    def test_noconfig_when_unprovisioned(self, tmp_path):
        assert _session({}, "CONFIG", tmp_path=tmp_path) == ["NOCONFIG"]

    def test_key_never_logged(self, tmp_path, caplog):
        with caplog.at_level("DEBUG"):
            _session({"device_config": CONFIG}, "CONFIG", tmp_path=tmp_path)
        assert "secret-key" not in caplog.text
        assert CONFIG.truck_id in caplog.text

    def test_pull_still_works_alongside(self, tmp_path):
        lines = _session({"device_config": CONFIG}, "PULL 5", tmp_path=tmp_path)
        assert lines == [protocol.END_LINE]


class TestServerAllowlist:
    def test_listed_peer_is_served_case_insensitively(self, tmp_path):
        lines = _session(
            {"device_config": CONFIG, "allowed_peers": ["aa:bb:cc:dd:ee:ff"]},
            "CONFIG",
            peer="AA:BB:CC:DD:EE:FF",
            tmp_path=tmp_path,
        )
        assert protocol.parse_config(lines[0]) == CONFIG.to_wire()

    @pytest.mark.parametrize("peer", ["11:22:33:44:55:66", None])
    def test_unlisted_or_unknown_peer_gets_nothing(self, tmp_path, peer):
        lines = _session(
            {"device_config": CONFIG, "allowed_peers": ["AA:BB:CC:DD:EE:FF"]},
            "CONFIG",
            peer=peer,
            tmp_path=tmp_path,
        )
        assert lines == []

    def test_empty_allowlist_means_no_restriction(self, tmp_path):
        lines = _session(
            {"device_config": CONFIG, "allowed_peers": ["", " "]},
            "CONFIG",
            peer="11:22:33:44:55:66",
            tmp_path=tmp_path,
        )
        assert len(lines) == 1


class _FakeSocket:
    def __init__(self, *args):
        self.options = []

    def setsockopt(self, *args):
        self.options.append(args)

    def bind(self, addr):
        self.bound = addr

    def listen(self, n):
        pass


class TestListenerSecurity:
    def _listener(self, monkeypatch, **kwargs):
        sockets = []

        def factory(*args):
            sock = _FakeSocket(*args)
            sockets.append(sock)
            return sock

        monkeypatch.setattr(bluetooth_transport.socket, "socket", factory)
        monkeypatch.setattr(bluetooth_transport.socket, "AF_BLUETOOTH", 31, raising=False)
        monkeypatch.setattr(bluetooth_transport.socket, "BTPROTO_RFCOMM", 3, raising=False)
        bluetooth_transport.BluetoothSppListener(channel=4, **kwargs)
        return sockets[0]

    def test_default_requires_an_encrypted_link(self, monkeypatch):
        sock = self._listener(monkeypatch)
        assert sock.options == [
            (bluetooth_transport.SOL_BLUETOOTH, bluetooth_transport.BT_SECURITY, b"\x02\x00")
        ]

    def test_level_is_configurable(self, monkeypatch):
        sock = self._listener(monkeypatch, security="LOW")
        assert sock.options[0][2] == b"\x01\x00"

    def test_unknown_level_raises_before_opening_a_socket(self, monkeypatch):
        with pytest.raises(ValueError, match="paranoid"):
            self._listener(monkeypatch, security="paranoid")
