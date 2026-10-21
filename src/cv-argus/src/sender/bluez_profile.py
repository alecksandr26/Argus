"""`BluezProfileListener` — the ESP32's Bluetooth server, run through the host's BlueZ daemon over
D-Bus instead of a raw RFCOMM socket inside the container (`SENDER_TRANSPORT=bluez`).

**Why not `BluetoothSppListener` in the container.** Linux creates Bluetooth sockets only in the
host's own network namespace; anywhere else `socket(AF_BLUETOOTH, ...)` fails with `EAFNOSUPPORT`
(checked: a container on Docker's default network gets exactly that). The Pi runs rootless
Docker (see `docker-compose.pi.yml`), where even `network_mode: host` is a separate namespace, so
no in-container socket can ever work there.

BlueZ's Profile API turns it around. `bluetoothd`, on the host, listens on the RFCOMM channel,
publishes the Serial Port service over SDP (so the ESP32 can also find the channel by lookup),
enforces pairing + encryption, and hands each accepted connection's socket to this process with
`Profile1.NewConnection`. A file descriptor passed over D-Bus keeps working across namespaces.
The container needs only the host's D-Bus socket mounted (`docker-compose.pi.yml`).

The same bus connection replaces the manual `bluetoothctl` steps:
- the adapter is powered on and made pairable;
- a `NoInputNoOutput` pairing agent accepts **only** the MACs in `ESP32_BT_MAC` and rejects (and
  logs) anything else, so with no allowlist nothing new can pair;
- an allowed ESP32 is marked trusted, so later reconnects need no agent at all.

`SenderServer`'s own peer allowlist still checks every session, so a device paired some other way
still never gets `CONFIG`.

If `bluetoothd` or the D-Bus daemon restarts, the registrations are made again (with a backoff).
Not yet run against a real Pi + ESP32; `tests/test_sender_bluez_profile.py` drives it against a
private `dbus-daemon` with a stand-in BlueZ.
"""

import asyncio
import logging
import queue
import socket
import threading
from collections.abc import Iterable

from dbus_fast import BusType, DBusError, Message, MessageType, Variant
from dbus_fast.aio import MessageBus
from dbus_fast.auth import UID_NOT_SPECIFIED, AuthExternal
from dbus_fast.service import ServiceInterface, method

from .bluetooth_transport import BluetoothSppTransport, security_level
from .transport import Listener, Transport

logger = logging.getLogger(__name__)

BLUEZ = "org.bluez"
SPP_UUID = "00001101-0000-1000-8000-00805f9b34fb"  # Serial Port Profile
AGENT_PATH = "/org/argus/agent"
PROFILE_PATH = "/org/argus/spp"
REJECTED = "org.bluez.Error.Rejected"

_MAX_BACKOFF_S = 30.0
# NameOwnerChanged for org.bluez = bluetoothd restarted and forgot our agent and profile.
_BLUEZ_OWNER_MATCH = (
    "type='signal',sender='org.freedesktop.DBus',interface='org.freedesktop.DBus',"
    "member='NameOwnerChanged',arg0='org.bluez'"
)


def mac_from_device_path(path: str) -> str | None:
    """`/org/bluez/hci0/dev_78_1C_3C_36_4A_22` -> `78:1C:3C:36:4A:22`; `None` if not a device."""
    _, _, tail = path.rpartition("/dev_")
    parts = tail.split("_")
    if len(parts) != 6 or not all(len(p) == 2 for p in parts):
        return None
    return ":".join(parts).upper()


def requires_authentication(security: str) -> bool:
    """`BLUETOOTH_SECURITY` -> the profile's `RequireAuthentication`. `high` (MITM-protected
    pairing) isn't offered: the ESP32 has no display or keypad, so it can only pair Just Works."""
    level = security_level(security)  # raises ValueError on an unknown name
    if level == 3:
        raise ValueError(
            "'high' needs MITM-protected pairing, which the ESP32 can't do -- use 'medium'"
        )
    return level == 2


class PairingPolicy:
    """Which devices may pair: exactly the `ESP32_BT_MAC` allowlist, compared case-insensitively.
    An empty allowlist allows none, so auto-pairing never accepts an unknown device."""

    def __init__(self, allowed_peers: Iterable[str] | None) -> None:
        self.allowed = frozenset(p.strip().upper() for p in (allowed_peers or ()) if p.strip())

    def allows(self, mac: str | None) -> bool:
        return mac is not None and mac.upper() in self.allowed

    def refusal(self, mac: str | None) -> str:
        if not self.allowed:
            return f"set ESP32_BT_MAC={mac} in .env if that is this truck's ESP32"
        return f"not in ESP32_BT_MAC ({', '.join(sorted(self.allowed))})"


class _Agent(ServiceInterface):
    """`org.bluez.Agent1` with `NoInputNoOutput`: no PIN or passkey ever, and every pairing or
    service request is answered from `PairingPolicy` alone."""

    def __init__(self, policy: PairingPolicy, on_allowed) -> None:
        super().__init__("org.bluez.Agent1")
        self._policy = policy
        self._on_allowed = on_allowed

    def _check(self, device: str, what: str) -> None:
        mac = mac_from_device_path(device)
        if not self._policy.allows(mac):
            logger.warning("bluetooth: refused %s from %s: %s", what, mac, self._policy.refusal(mac))
            raise DBusError(REJECTED, f"{mac} is not this truck's ESP32")
        logger.info("bluetooth: accepted %s from %s", what, mac)
        self._on_allowed(device)

    @method()
    async def Release(self) -> None:
        pass

    @method()
    async def RequestPinCode(self, device: "o") -> "s":
        raise DBusError(REJECTED, "no PIN: Just Works pairing only")

    @method()
    async def DisplayPinCode(self, device: "o", pincode: "s") -> None:
        pass

    @method()
    async def RequestPasskey(self, device: "o") -> "u":
        raise DBusError(REJECTED, "no passkey: Just Works pairing only")

    @method()
    async def DisplayPasskey(self, device: "o", passkey: "u", entered: "q") -> None:
        pass

    @method()
    async def RequestConfirmation(self, device: "o", passkey: "u") -> None:
        self._check(device, "pairing")

    @method()
    async def RequestAuthorization(self, device: "o") -> None:
        self._check(device, "pairing")

    @method()
    async def AuthorizeService(self, device: "o", uuid: "s") -> None:
        self._check(device, "service")

    @method()
    async def Cancel(self) -> None:
        pass


class _Profile(ServiceInterface):
    """`org.bluez.Profile1`: bluetoothd calls `NewConnection` with the accepted RFCOMM socket."""

    def __init__(self, on_connection) -> None:
        super().__init__("org.bluez.Profile1")
        self._on_connection = on_connection

    @method()
    async def Release(self) -> None:
        pass

    @method()
    async def NewConnection(self, device: "o", fd: "h", fd_properties: "a{sv}") -> None:
        self._on_connection(device, fd)

    @method()
    async def RequestDisconnection(self, device: "o") -> None:
        pass  # the session ends on its own: SenderServer closes the transport


class BluezError(OSError):
    """A BlueZ D-Bus call answered with an error."""


async def _call(bus: MessageBus, path: str, interface: str, member: str, signature: str = "",
                body: list | None = None, destination: str = BLUEZ) -> Message:
    reply = await bus.call(Message(destination=destination, path=path, interface=interface,
                                   member=member, signature=signature, body=body or []))
    if reply.message_type == MessageType.ERROR:
        detail = reply.body[0] if reply.body else ""
        raise BluezError(f"{interface}.{member} on {path}: {reply.error_name}: {detail}")
    return reply


async def _set_property(bus: MessageBus, path: str, interface: str, name: str, value: Variant) -> None:
    await _call(bus, path, "org.freedesktop.DBus.Properties", "Set", "ssv", [interface, name, value])


class BluezProfileListener(Listener):
    """A `Listener` whose connections come from BlueZ's `Profile1.NewConnection`. Construction
    connects and registers everything, or raises `OSError` (so `main.py` stops with a clear reason
    rather than serving nothing); after that, a lost bus or a restarted bluetoothd is recovered in
    the background."""

    def __init__(self, channel: int = 4, security: str = "medium",
                 allowed_peers: Iterable[str] | None = None, *, adapter: str = "hci0",
                 bus_address: str | None = None, setup_timeout: float = 15.0) -> None:
        self._channel = channel
        self._require_auth = requires_authentication(security)  # validate before any I/O
        self._policy = PairingPolicy(allowed_peers)
        self._adapter_path = f"/org/bluez/{adapter}"
        self._bus_address = bus_address
        self._connections: queue.Queue[Transport] = queue.Queue()
        self._ready = threading.Event()
        self._setup_error: BaseException | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._stopping: asyncio.Event | None = None
        self._pending: set[asyncio.Task] = set()
        self._bus: MessageBus | None = None
        self._thread = threading.Thread(target=self._run, name="bluez", daemon=True)
        self._thread.start()

        if not self._ready.wait(setup_timeout):
            self.close()
            raise OSError(f"BlueZ didn't answer on D-Bus within {setup_timeout:.0f}s")
        if self._setup_error is not None:
            error = self._setup_error
            self.close()
            raise OSError(f"Bluetooth setup through BlueZ failed: {error}") from error
        if not self._policy.allowed:
            logger.warning(
                "bluetooth: ESP32_BT_MAC is unset, so no new device can pair. Set it to this "
                "truck's ESP32 Bluetooth MAC (the ESP32 prints it at boot)."
            )

    # --- Listener ------------------------------------------------------------------------

    def accept(self, timeout: float | None = None) -> Transport | None:
        try:
            return self._connections.get(timeout=timeout)
        except queue.Empty:
            return None

    def close(self) -> None:
        loop, stopping = self._loop, self._stopping
        if loop is not None and stopping is not None and not loop.is_closed():
            loop.call_soon_threadsafe(stopping.set)
        if threading.current_thread() is not self._thread:
            self._thread.join(timeout=5.0)
        while True:
            try:
                self._connections.get_nowait().close()
            except queue.Empty:
                break

    # --- the D-Bus side, all on this listener's own event loop thread --------------------

    def _run(self) -> None:
        loop = asyncio.new_event_loop()
        self._loop = loop
        try:
            loop.run_until_complete(self._serve())
        finally:
            loop.close()

    async def _serve(self) -> None:
        self._stopping = asyncio.Event()
        backoff = 1.0
        first = True
        while not self._stopping.is_set():
            lost = asyncio.Event()
            bus = None
            try:
                bus = await self._connect(lost)
                await self._register(bus)
                self._bus = bus
                logger.info(
                    "bluetooth: ready through BlueZ -- serial port on RFCOMM channel %d (%s), "
                    "pairing allowed for %s", self._channel,
                    "paired + encrypted" if self._require_auth else "no pairing required",
                    ", ".join(sorted(self._policy.allowed)) or "nobody",
                )
                if first:
                    first = False
                    self._ready.set()
                backoff = 1.0
                await self._wait_any(bus.wait_for_disconnect(), lost.wait(), self._stopping.wait())
                if not self._stopping.is_set():
                    logger.warning("bluetooth: lost BlueZ (bluetoothd or D-Bus restarted), re-registering")
            except Exception as exc:
                if first:
                    self._setup_error = exc
                    self._ready.set()
                    return
                logger.warning("bluetooth: BlueZ setup failed (%s), retrying in %.0fs", exc, backoff)
            finally:
                self._bus = None
                if bus is not None:
                    await self._unregister(bus)
                    bus.disconnect()
            if self._stopping.is_set():
                break
            await self._wait_any(asyncio.sleep(backoff), self._stopping.wait())
            backoff = min(backoff * 2, _MAX_BACKOFF_S)

    async def _connect(self, lost: asyncio.Event) -> MessageBus:
        # UID_NOT_SPECIFIED: let dbus-daemon take our identity from the socket's credentials. In
        # rootless Docker the container's uid 0 is a different uid on the host, so claiming
        # "uid 0" (the default) would be rejected.
        auth = AuthExternal(uid=UID_NOT_SPECIFIED)
        if self._bus_address:
            bus = MessageBus(bus_address=self._bus_address, negotiate_unix_fd=True, auth=auth)
        else:
            bus = MessageBus(bus_type=BusType.SYSTEM, negotiate_unix_fd=True, auth=auth)
        await bus.connect()

        def _watch_bluez(msg: Message) -> None:
            if msg.member == "NameOwnerChanged" and msg.body and msg.body[0] == BLUEZ:
                lost.set()

        bus.add_message_handler(_watch_bluez)
        await _call(bus, "/org/freedesktop/DBus", "org.freedesktop.DBus", "AddMatch", "s",
                    [_BLUEZ_OWNER_MATCH], destination="org.freedesktop.DBus")
        return bus

    async def _register(self, bus: MessageBus) -> None:
        adapter = self._adapter_path
        await _set_property(bus, adapter, "org.bluez.Adapter1", "Powered", Variant("b", True))
        await _set_property(bus, adapter, "org.bluez.Adapter1", "Pairable", Variant("b", True))
        await _set_property(bus, adapter, "org.bluez.Adapter1", "PairableTimeout", Variant("u", 0))

        bus.export(AGENT_PATH, _Agent(self._policy, on_allowed=lambda dev: self._trust(bus, dev)))
        await _call(bus, "/org/bluez", "org.bluez.AgentManager1", "RegisterAgent", "os",
                    [AGENT_PATH, "NoInputNoOutput"])
        await _call(bus, "/org/bluez", "org.bluez.AgentManager1", "RequestDefaultAgent", "o",
                    [AGENT_PATH])

        bus.export(PROFILE_PATH, _Profile(lambda dev, fd: self._on_connection(bus, dev, fd)))
        await _call(bus, "/org/bluez", "org.bluez.ProfileManager1", "RegisterProfile", "osa{sv}", [
            PROFILE_PATH, SPP_UUID, {
                "Name": Variant("s", "Argus ESP32 link"),
                "Role": Variant("s", "server"),
                "Channel": Variant("q", self._channel),
                "RequireAuthentication": Variant("b", self._require_auth),
                # Who may connect is SenderServer's allowlist, checked on every session.
                "RequireAuthorization": Variant("b", False),
                "AutoConnect": Variant("b", False),
            },
        ])

    async def _unregister(self, bus: MessageBus) -> None:
        """Best effort: bluetoothd also drops both when our bus connection closes."""
        if not bus.connected:
            return
        for interface, member, path in (
            ("org.bluez.ProfileManager1", "UnregisterProfile", PROFILE_PATH),
            ("org.bluez.AgentManager1", "UnregisterAgent", AGENT_PATH),
        ):
            try:
                await _call(bus, "/org/bluez", interface, member, "o", [path])
            except Exception:
                pass

    def _on_connection(self, bus: MessageBus, device: str, fd: int) -> None:
        mac = mac_from_device_path(device)
        sock = socket.socket(fileno=fd)  # we own this fd now; the transport closes it
        logger.info("bluetooth: %s connected", mac)
        if self._policy.allows(mac):
            self._trust(bus, device)
        self._connections.put(BluetoothSppTransport(sock, peer=mac))

    def _trust(self, bus: MessageBus, device: str) -> None:
        """Marks an allowed ESP32 trusted, so its reconnects skip the agent. Fire and forget."""

        async def _set_trusted() -> None:
            try:
                await _set_property(bus, device, "org.bluez.Device1", "Trusted", Variant("b", True))
            except Exception as exc:
                logger.debug("bluetooth: couldn't mark %s trusted: %s", device, exc)

        task = asyncio.ensure_future(_set_trusted())
        self._pending.add(task)
        task.add_done_callback(self._pending.discard)

    @staticmethod
    async def _wait_any(*aws) -> None:
        tasks = [asyncio.ensure_future(a) for a in aws]
        try:
            await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for task in tasks:
                task.cancel()
