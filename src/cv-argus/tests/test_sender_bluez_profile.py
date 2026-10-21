"""`sender/bluez_profile.py`: the pure helpers, then `BluezProfileListener` against a private
`dbus-daemon` with a stand-in BlueZ.

The stand-in owns `org.bluez` on that bus and implements just what the listener calls
(`AgentManager1`, `ProfileManager1`, the adapter's and a device's properties). It then plays
bluetoothd: it asks the listener's agent to approve pairings and hands it a connected socket with
`Profile1.NewConnection`, a real file descriptor passed over D-Bus. What this can't cover is real
radio pairing and the real bluetoothd's behaviour; that needs the Pi and the ESP32.
"""

import asyncio
import shutil
import socket
import subprocess
import threading
import time

import pytest

pytest.importorskip("dbus_fast")

from dbus_fast import Message, MessageType, Variant  # noqa: E402
from dbus_fast.aio import MessageBus  # noqa: E402
from dbus_fast.service import PropertyAccess, ServiceInterface, dbus_property, method  # noqa: E402

from cv_argus.sender import bluez_profile  # noqa: E402
from cv_argus.sender.bluez_profile import (  # noqa: E402
    AGENT_PATH,
    PROFILE_PATH,
    SPP_UUID,
    BluezProfileListener,
    PairingPolicy,
    mac_from_device_path,
    requires_authentication,
)

ESP32_MAC = "78:1C:3C:36:4A:22"
ESP32_PATH = "/org/bluez/hci0/dev_78_1C_3C_36_4A_22"
STRANGER_PATH = "/org/bluez/hci0/dev_11_22_33_44_55_66"


class TestHelpers:
    def test_mac_from_device_path(self):
        assert mac_from_device_path(ESP32_PATH) == ESP32_MAC
        assert mac_from_device_path("/org/bluez/hci0/dev_78_1c_3c_36_4a_22") == ESP32_MAC
        assert mac_from_device_path("/org/bluez/hci0") is None
        assert mac_from_device_path("/org/bluez/hci0/dev_78_1C_3C") is None

    def test_security_maps_to_require_authentication(self):
        assert requires_authentication("medium") is True
        assert requires_authentication(" LOW ") is False
        with pytest.raises(ValueError, match="MITM"):
            requires_authentication("high")
        with pytest.raises(ValueError, match="Unknown"):
            requires_authentication("paranoid")

    def test_policy_is_the_allowlist_only(self):
        policy = PairingPolicy([" 78:1c:3c:36:4a:22 ", ""])
        assert policy.allows(ESP32_MAC)
        assert policy.allows(ESP32_MAC.lower())
        assert not policy.allows("11:22:33:44:55:66")
        assert not policy.allows(None)

    def test_empty_policy_allows_nobody_and_says_how_to_fix_it(self):
        policy = PairingPolicy(None)
        assert not policy.allows(ESP32_MAC)
        assert f"ESP32_BT_MAC={ESP32_MAC}" in policy.refusal(ESP32_MAC)


# --- a private bus with a stand-in BlueZ -------------------------------------------------


@pytest.fixture
def bus_address():
    if shutil.which("dbus-daemon") is None:
        pytest.skip("dbus-daemon not installed")
    proc = subprocess.Popen(
        ["dbus-daemon", "--session", "--nofork", "--print-address"],
        stdout=subprocess.PIPE, text=True,
    )
    address = proc.stdout.readline().strip()
    yield address
    proc.terminate()
    proc.wait(timeout=5)


class _Recorder:
    def __init__(self):
        self.calls = []
        self.lock = threading.Lock()

    def add(self, *call):
        with self.lock:
            self.calls.append(call)

    def named(self, member):
        with self.lock:
            return [c for c in self.calls if c[0] == member]


class _Managers(ServiceInterface):
    def __init__(self, name, rec):
        super().__init__(name)
        self._rec = rec

    @method()
    def RegisterAgent(self, agent: "o", capability: "s"):  # noqa: F821, N802
        self._rec.add("RegisterAgent", agent, capability)

    @method()
    def UnregisterAgent(self, agent: "o"):  # noqa: F821, N802
        self._rec.add("UnregisterAgent", agent)

    @method()
    def RequestDefaultAgent(self, agent: "o"):  # noqa: F821, N802
        self._rec.add("RequestDefaultAgent", agent)

    @method()
    def RegisterProfile(self, profile: "o", uuid: "s", options: "a{sv}"):  # noqa: F821, N802
        self._rec.add("RegisterProfile", profile, uuid, {k: v.value for k, v in options.items()})

    @method()
    def UnregisterProfile(self, profile: "o"):  # noqa: F821, N802
        self._rec.add("UnregisterProfile", profile)


class _Adapter(ServiceInterface):
    """An older BlueZ: no Connectable property, so the listener goes through Discoverable."""

    def __init__(self):
        super().__init__("org.bluez.Adapter1")
        self.powered, self.pairable, self.timeout = False, False, 180
        self.discoverable, self.discoverable_timeout = False, 180
        self.connectable = None

    @dbus_property(access=PropertyAccess.READWRITE)
    def Discoverable(self) -> "b":  # noqa: F821, N802
        return self.discoverable

    @Discoverable.setter
    def Discoverable(self, value: "b"):  # noqa: F821, N802
        self.discoverable = value

    @dbus_property(access=PropertyAccess.READWRITE)
    def DiscoverableTimeout(self) -> "u":  # noqa: F821, N802
        return self.discoverable_timeout

    @DiscoverableTimeout.setter
    def DiscoverableTimeout(self, value: "u"):  # noqa: F821, N802
        self.discoverable_timeout = value

    @dbus_property(access=PropertyAccess.READWRITE)
    def Powered(self) -> "b":  # noqa: F821, N802
        return self.powered

    @Powered.setter
    def Powered(self, value: "b"):  # noqa: F821, N802
        self.powered = value

    @dbus_property(access=PropertyAccess.READWRITE)
    def Pairable(self) -> "b":  # noqa: F821, N802
        return self.pairable

    @Pairable.setter
    def Pairable(self, value: "b"):  # noqa: F821, N802
        self.pairable = value

    @dbus_property(access=PropertyAccess.READWRITE)
    def PairableTimeout(self) -> "u":  # noqa: F821, N802
        return self.timeout

    @PairableTimeout.setter
    def PairableTimeout(self, value: "u"):  # noqa: F821, N802
        self.timeout = value


class _ModernAdapter(_Adapter):
    """A BlueZ with Adapter1.Connectable."""

    def __init__(self):
        super().__init__()
        self.connectable = False

    @dbus_property(access=PropertyAccess.READWRITE)
    def Connectable(self) -> "b":  # noqa: F821, N802
        return self.connectable

    @Connectable.setter
    def Connectable(self, value: "b"):  # noqa: F821, N802
        self.connectable = value


class _Device(ServiceInterface):
    def __init__(self):
        super().__init__("org.bluez.Device1")
        self.trusted = False

    @dbus_property(access=PropertyAccess.READWRITE)
    def Trusted(self) -> "b":  # noqa: F821, N802
        return self.trusted

    @Trusted.setter
    def Trusted(self, value: "b"):  # noqa: F821, N802
        self.trusted = value


class FakeBluez:
    """Owns `org.bluez` on the test bus from its own thread and event loop."""

    def __init__(self, address, modern=False):
        self.address = address
        self.rec = _Recorder()
        self.adapter = _ModernAdapter() if modern else _Adapter()
        self.device = _Device()
        self.loop = asyncio.new_event_loop()
        self.bus = None
        threading.Thread(target=self.loop.run_forever, daemon=True).start()
        self.run(self._start())

    def run(self, coro, timeout=5.0):
        return asyncio.run_coroutine_threadsafe(coro, self.loop).result(timeout)

    async def _start(self):
        self.bus = await MessageBus(bus_address=self.address, negotiate_unix_fd=True).connect()
        self.bus.export("/org/bluez", _Managers("org.bluez.AgentManager1", self.rec))
        self.bus.export("/org/bluez", _Managers("org.bluez.ProfileManager1", self.rec))
        self.bus.export("/org/bluez/hci0", self.adapter)
        self.bus.export(ESP32_PATH, self.device)
        await self.bus.request_name("org.bluez")

    async def call_agent(self, listener_name, member, signature, body):
        return await self.bus.call(Message(
            destination=listener_name, path=AGENT_PATH, interface="org.bluez.Agent1",
            member=member, signature=signature, body=body,
        ))

    async def new_connection(self, listener_name, device_path, fd):
        return await self.bus.call(Message(
            destination=listener_name, path=PROFILE_PATH, interface="org.bluez.Profile1",
            member="NewConnection", signature="oha{sv}", body=[device_path, 0, {}],
            unix_fds=[fd],
        ))

    async def restart(self):
        await self.bus.release_name("org.bluez")
        await self.bus.request_name("org.bluez")

    def stop(self):
        self.run(self._stop())
        self.loop.call_soon_threadsafe(self.loop.stop)

    async def _stop(self):
        self.bus.disconnect()


@pytest.fixture
def bluez(bus_address):
    fake = FakeBluez(bus_address)
    yield fake
    fake.stop()


@pytest.fixture
def listener(bus_address, bluez):
    lst = BluezProfileListener(channel=4, security="medium", allowed_peers=[ESP32_MAC.lower()],
                               bus_address=bus_address, setup_timeout=10.0)
    yield lst
    lst.close()


def _wait_for(predicate, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return predicate()


class TestAgainstABus:
    def test_setup_configures_the_adapter_and_registers_agent_and_profile(self, bluez, listener):
        assert bluez.adapter.powered is True
        assert bluez.adapter.pairable is True
        assert bluez.adapter.timeout == 0
        assert bluez.rec.named("RegisterAgent") == [("RegisterAgent", AGENT_PATH, "NoInputNoOutput")]
        assert bluez.rec.named("RequestDefaultAgent") == [("RequestDefaultAgent", AGENT_PATH)]
        [(_, path, uuid, options)] = bluez.rec.named("RegisterProfile")
        assert (path, uuid) == (PROFILE_PATH, SPP_UUID)
        assert options["Role"] == "server"
        assert options["Channel"] == 4
        assert options["RequireAuthentication"] is True
        assert options["AutoConnect"] is False

    def test_older_bluez_is_made_connectable_through_discoverable(self, bluez, listener):
        # Seen on the real Pi: not connectable until something paired, so the ESP32's first
        # connection was ignored.
        assert bluez.adapter.discoverable is True
        assert bluez.adapter.discoverable_timeout == 0

    def test_agent_accepts_only_the_allowlisted_esp32(self, bluez, listener):
        name = listener._bus.unique_name
        ok = bluez.run(bluez.call_agent(name, "RequestConfirmation", "ou", [ESP32_PATH, 123456]))
        assert ok.message_type == MessageType.METHOD_RETURN
        no = bluez.run(bluez.call_agent(name, "RequestConfirmation", "ou", [STRANGER_PATH, 123456]))
        assert no.message_type == MessageType.ERROR
        assert no.error_name == "org.bluez.Error.Rejected"
        for member, sig, body in (
            ("RequestAuthorization", "o", [STRANGER_PATH]),
            ("AuthorizeService", "os", [STRANGER_PATH, SPP_UUID]),
            ("RequestPinCode", "o", [ESP32_PATH]),
            ("RequestPasskey", "o", [ESP32_PATH]),
        ):
            reply = bluez.run(bluez.call_agent(name, member, sig, body))
            assert reply.error_name == "org.bluez.Error.Rejected", member
        assert bluez.run(bluez.call_agent(name, "RequestAuthorization", "o", [ESP32_PATH])
                         ).message_type == MessageType.METHOD_RETURN

    def test_accepted_pairing_marks_the_esp32_trusted(self, bluez, listener):
        bluez.run(bluez.call_agent(listener._bus.unique_name, "RequestAuthorization", "o", [ESP32_PATH]))
        assert _wait_for(lambda: bluez.device.trusted)

    def test_new_connection_becomes_a_working_transport(self, bluez, listener):
        ours, esp32 = socket.socketpair()
        try:
            reply = bluez.run(bluez.new_connection(listener._bus.unique_name, ESP32_PATH, ours.fileno()))
            assert reply.message_type == MessageType.METHOD_RETURN
            ours.close()  # bluetoothd keeps no copy either; the listener owns the passed fd

            transport = listener.accept(timeout=5.0)
            assert transport is not None
            assert transport.peer == ESP32_MAC
            esp32.sendall(b"PULL 5\n")
            assert transport.readline(timeout=5.0) == b"PULL 5"
            transport.write(b"END\n")
            esp32.settimeout(5.0)
            assert esp32.recv(16) == b"END\n"
            transport.close()
            assert esp32.recv(16) == b""  # closing the transport closes the passed socket
        finally:
            esp32.close()

    def test_accept_times_out_with_none(self, listener):
        assert listener.accept(timeout=0.1) is None

    def test_a_bluez_restart_registers_everything_again(self, bluez, listener):
        bluez.run(bluez.restart())
        assert _wait_for(lambda: len(bluez.rec.named("RegisterProfile")) >= 2, timeout=8.0)
        assert len(bluez.rec.named("RegisterAgent")) >= 2

    def test_close_unregisters(self, bus_address, bluez):
        lst = BluezProfileListener(allowed_peers=[ESP32_MAC], bus_address=bus_address, setup_timeout=10.0)
        lst.close()
        assert _wait_for(lambda: bluez.rec.named("UnregisterProfile"))
        assert bluez.rec.named("UnregisterAgent")


def test_no_bluez_on_the_bus_fails_construction_clearly(bus_address):
    with pytest.raises(OSError, match="Bluetooth setup through BlueZ failed"):
        BluezProfileListener(allowed_peers=[ESP32_MAC], bus_address=bus_address, setup_timeout=10.0)


def test_unset_allowlist_warns_how_to_fix_it(bus_address, bluez, caplog):
    with caplog.at_level("WARNING", logger=bluez_profile.__name__):
        lst = BluezProfileListener(allowed_peers=None, bus_address=bus_address, setup_timeout=10.0)
    try:
        assert "ESP32_BT_MAC is unset" in caplog.text
        reply = bluez.run(bluez.call_agent(lst._bus.unique_name, "RequestAuthorization", "o", [ESP32_PATH]))
        assert reply.error_name == "org.bluez.Error.Rejected"
    finally:
        lst.close()


def test_invalid_security_fails_before_any_bus_io():
    with pytest.raises(ValueError):
        BluezProfileListener(security="high", bus_address="unix:path=/nonexistent")


def test_newer_bluez_is_made_connectable_directly(bus_address):
    fake = FakeBluez(bus_address, modern=True)
    try:
        lst = BluezProfileListener(allowed_peers=[ESP32_MAC], bus_address=bus_address,
                                   setup_timeout=10.0)
        lst.close()
        assert fake.adapter.connectable is True
        assert fake.adapter.discoverable is False  # not needed when Connectable exists
    finally:
        fake.stop()
