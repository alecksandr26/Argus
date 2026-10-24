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
    device_config,
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


class TestNetworkSettings:
    NETWORK = {
        "ARGUS_WIFI_SSID": "Casa ",  # a real SSID with a trailing space
        "ARGUS_WIFI_PASSWORD": "wifi-secret",
        "ARGUS_GPRS_APN": "internet.itelcel.com",
        "ARGUS_GPRS_USER": "webgprs",
        "ARGUS_GPRS_PASSWORD": "webgprs2002",
    }

    def test_network_settings_are_sent_verbatim(self):
        wire = DeviceConfig.from_env({**ENV, **self.NETWORK}).to_wire()
        assert wire["wifi_ssid"] == "Casa "  # not stripped
        assert wire["wifi_password"] == "wifi-secret"
        assert wire["gprs_apn"] == "internet.itelcel.com"
        assert wire["gprs_user"] == "webgprs"
        assert wire["gprs_password"] == "webgprs2002"

    def test_unset_or_empty_network_settings_are_left_out(self):
        wire = DeviceConfig.from_env({**ENV, "ARGUS_GPRS_APN": ""}).to_wire()
        assert set(wire) == {"truck_id", "device_api_key", "backend_url"}

    def test_network_settings_alone_are_not_a_config(self):
        # Without credentials the ESP32 can't reach the backend anyway: still NOCONFIG.
        assert DeviceConfig.from_env(self.NETWORK) is None

    def test_repr_masks_the_passwords(self):
        text = repr(DeviceConfig.from_env({**ENV, **self.NETWORK}))
        assert "wifi-secret" not in text and "webgprs2002" not in text
        assert "internet.itelcel.com" in text

    def test_served_in_the_config_reply(self, tmp_path):
        config = DeviceConfig.from_env({**ENV, **self.NETWORK})
        lines = _session({"device_config": config}, "CONFIG", tmp_path=tmp_path)
        assert protocol.parse_config(lines[0])["wifi_ssid"] == "Casa "


class TestSmsFields:
    """`ARGUS_TRUCK_PLATE`/`ARGUS_ALERT_PHONE` (grip-alarm contract C1.3): optional, stripped,
    outside the credential check; an invalid phone is warned about and left out."""

    PHONE = "+523312345678"
    SMS = {"ARGUS_TRUCK_PLATE": "  JAL-1234 ", "ARGUS_ALERT_PHONE": f" {PHONE} "}

    def test_from_env_with_phone_and_plate(self):
        config = DeviceConfig.from_env({**ENV, **self.SMS})
        assert config.truck_plate == "JAL-1234"  # stripped
        assert config.alert_phone == self.PHONE  # stripped

    def test_from_env_without_them(self):
        assert CONFIG.truck_plate is None and CONFIG.alert_phone is None
        config = DeviceConfig.from_env({**ENV, "ARGUS_TRUCK_PLATE": " ", "ARGUS_ALERT_PHONE": ""})
        assert config.truck_plate is None and config.alert_phone is None

    @pytest.mark.parametrize(
        "phone",
        ["3312345678", "+0523312345678", "+52 33 1234 5678", "+5233", "+5233123456789012", "abc"],
    )
    def test_invalid_phone_is_warned_masked_and_left_out(self, phone, caplog):
        with caplog.at_level("WARNING"):
            config = DeviceConfig.from_env({**ENV, "ARGUS_ALERT_PHONE": phone})
        assert config is not None  # the credentials still load
        assert config.alert_phone is None
        assert "ARGUS_ALERT_PHONE" in caplog.text
        assert phone not in caplog.text

    def test_shortest_and_longest_e164_are_accepted(self):
        for phone in ("+12345678", "+123456789012345"):
            assert DeviceConfig.from_env({**ENV, "ARGUS_ALERT_PHONE": phone}).alert_phone == phone

    def test_phone_with_a_trailing_newline_is_not_e164(self):
        # `re.match` with `$` accepts "+52...\n"; the backend, UI and firmware checks don't, and
        # the mask mustn't treat it as a valid number either.
        assert device_config._alert_phone(self.PHONE + "\n") is None
        assert device_config.mask_phone(self.PHONE + "\n").startswith("*")

    def test_plate_longer_than_the_esp32_takes_is_kept_but_warned(self, caplog):
        plate = "X" * (device_config.ESP32_MAX_PLATE_LEN + 1)
        with caplog.at_level("WARNING"):
            config = DeviceConfig.from_env({**ENV, "ARGUS_TRUCK_PLATE": plate})
        assert config.truck_plate == plate
        assert "ARGUS_TRUCK_PLATE" in caplog.text

    def test_plate_of_16_is_not_warned(self, caplog):
        plate = "X" * device_config.ESP32_MAX_PLATE_LEN
        with caplog.at_level("WARNING"):
            config = DeviceConfig.from_env({**ENV, "ARGUS_TRUCK_PLATE": plate})
        assert config.truck_plate == plate
        assert "ARGUS_TRUCK_PLATE" not in caplog.text

    def test_partial_credential_rule_unaffected(self):
        # Plate and phone alone are not a config...
        assert DeviceConfig.from_env(self.SMS) is None
        # ...and don't complete a partial one either.
        with pytest.raises(ValueError, match="ARGUS_DEVICE_API_KEY"):
            DeviceConfig.from_env({**ENV, **self.SMS, "ARGUS_DEVICE_API_KEY": ""})

    def test_to_wire_carries_them_only_when_set(self):
        wire = DeviceConfig.from_env({**ENV, **self.SMS}).to_wire()
        assert wire["truck_plate"] == "JAL-1234"
        assert wire["alert_phone"] == self.PHONE
        assert "truck_plate" not in CONFIG.to_wire() and "alert_phone" not in CONFIG.to_wire()
        plate_only = DeviceConfig.from_env({**ENV, "ARGUS_TRUCK_PLATE": "JAL-1234"}).to_wire()
        assert plate_only["truck_plate"] == "JAL-1234" and "alert_phone" not in plate_only

    def test_repr_masks_the_phone_keeping_the_last_4_digits(self):
        text = repr(DeviceConfig.from_env({**ENV, **self.SMS}))
        assert self.PHONE not in text
        assert "alert_phone='+52******5678'" in text
        assert "truck_plate='JAL-1234'" in text
        assert "secret-key" not in text  # the key is still hidden
        assert "alert_phone" not in repr(CONFIG)

    @pytest.mark.parametrize(
        ("phone", "masked"),
        [
            ("+523312345678", "+52******5678"),
            ("+12345678", "+12**5678"),
            ("abcdef", "**cdef"),  # not E.164: only the last 4 stay
            ("12", "**"),
        ],
    )
    def test_mask_phone(self, phone, masked):
        assert device_config.mask_phone(phone) == masked

    def test_served_in_the_config_reply_and_phone_never_logged(self, tmp_path, caplog):
        config = DeviceConfig.from_env({**ENV, **self.SMS})
        with caplog.at_level("DEBUG"):
            lines = _session({"device_config": config}, "CONFIG", tmp_path=tmp_path)
        assert len(lines) == 1
        payload = protocol.parse_config(lines[0])
        assert payload["truck_plate"] == "JAL-1234"
        assert payload["alert_phone"] == self.PHONE
        assert payload == config.to_wire()
        assert self.PHONE not in caplog.text


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
        # v2: a pull session ends with the ESP32's DONE and the Pi's OK line.
        lines = _session({"device_config": CONFIG}, "PULL 5\nDONE", tmp_path=tmp_path)
        assert lines == [protocol.format_end(0, 0), protocol.format_result(0, 0, 0, 0, 0)]


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

    def test_binds_any_adapter_as_a_real_address(self, monkeypatch):
        # CPython rejects "" for RFCOMM ("bad bluetooth address"); it must be six hex pairs.
        sock = self._listener(monkeypatch)
        assert sock.bound == ("00:00:00:00:00:00", 4)

    def test_level_is_configurable(self, monkeypatch):
        sock = self._listener(monkeypatch, security="LOW")
        assert sock.options[0][2] == b"\x01\x00"

    def test_unknown_level_raises_before_opening_a_socket(self, monkeypatch):
        with pytest.raises(ValueError, match="paranoid"):
            self._listener(monkeypatch, security="paranoid")
