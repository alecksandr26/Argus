#!/usr/bin/env python3
"""The Pi's Bluetooth server for the ESP32, run on the Pi *host* instead of inside the container.

The Pi is the server and the ESP32 the client: every poll, the ESP32 opens an RFCOMM connection,
sends `PULL`, gets the queued readings, and `ACK`s them (`src/sender/protocol.py`). This runs that
server -- cv-argus's own `SenderServer` -- against the SQLite queue the container writes, shared
through `./data` (`docker-compose.bt.yml`).

Why on the host: Linux only creates Bluetooth sockets in the host's own network namespace, and
rootless Docker's `network_mode: host` is a separate namespace, so the container can't open one.
This needs nothing but the Pi's own `python3` (stdlib + `src/`), no pip install.

    python3 scripts/pi_bt_server.py                    # serve ./data/buffer.sqlite3, RFCOMM ch. 4
    python3 scripts/pi_bt_server.py --security low     # bench: accept an unpaired ESP32
    python3 scripts/pi_bt_server.py --inject critical  # queue one fake CRITICAL reading and exit

`scripts/demo.sh --bluetooth` starts this next to the camera pipeline. On startup it prints the
Pi's Bluetooth MAC in `config.h` form (`PI_BT_MAC_ADDRESS`) and which devices are paired; then it
logs when the ESP32 connects, when it goes quiet, and each severity the ESP32 acknowledged.
"""

import argparse
import importlib.util
import logging
import os
import re
import signal
import socket
import subprocess
import sys
import threading
from pathlib import Path

MODULE_ROOT = Path(__file__).resolve().parent.parent  # src/cv-argus/
DEFAULT_BUFFER = MODULE_ROOT / "data" / "buffer.sqlite3"
# Where docker-compose.yml loads the truck's credentials and the ESP32's network from.
ENV_FILES = (MODULE_ROOT / "argus-device.env", MODULE_ROOT / ".env")

logger = logging.getLogger("pi_bt_server")


def _import_cv_argus():
    """Binds `cv_argus` to `src/` the way setup.py's package_dir does (as tests/conftest.py does),
    so this runs from a bare checkout. Only stdlib-only subpackages are imported below."""
    if importlib.util.find_spec("cv_argus") is None:
        src = MODULE_ROOT / "src"
        spec = importlib.util.spec_from_file_location(
            "cv_argus", src / "__init__.py", submodule_search_locations=[str(src)]
        )
        module = importlib.util.module_from_spec(spec)
        sys.modules["cv_argus"] = module
        spec.loader.exec_module(module)


def _load_env_files() -> None:
    """Copies `ARGUS_*` from argus-device.env / .env into the environment, so the ESP32 gets
    the same `CONFIG` it would from the container. Values already in the environment win."""
    for path in ENV_FILES:
        if not path.is_file():
            continue
        for line in path.read_text().splitlines():
            key, sep, value = line.partition("=")
            key = key.strip()
            if sep and key.startswith("ARGUS_") and not line.lstrip().startswith("#"):
                os.environ.setdefault(key, value)


def _bluetoothctl(*args: str) -> str:
    try:
        return subprocess.run(
            ["bluetoothctl", *args], capture_output=True, text=True, timeout=5
        ).stdout
    except (OSError, subprocess.TimeoutExpired):
        return ""


def _report_adapter(security: str) -> None:
    """The Pi's adapter and paired devices, from bluetoothctl. Informational only."""
    show = _bluetoothctl("show")
    match = re.search(r"Controller ([0-9A-F:]{17})", show)
    if match is None:
        logger.warning("No Bluetooth adapter found (bluetoothctl show) -- is Bluetooth enabled?")
        return
    mac = match.group(1)
    as_c = ", ".join(f"0x{part}" for part in mac.split(":"))
    logger.info("Pi Bluetooth adapter %s -- in the ESP32's config.h:", mac)
    logger.info("    #define PI_BT_MAC_ADDRESS { %s }", as_c)
    if "Powered: yes" not in show:
        logger.warning("The adapter is powered off -- run: bluetoothctl power on")

    paired = _bluetoothctl("devices", "Paired") or _bluetoothctl("paired-devices")
    devices = re.findall(r"Device ([0-9A-F:]{17}) (.*)", paired)
    if devices:
        for dev_mac, name in devices:
            logger.info("Paired: %s %s", dev_mac, name)
    elif security != "low":
        logger.warning(
            "No paired devices. The ESP32 can't connect at security=%s until it's paired "
            "(README 'Bluetooth link test'), or run with --security low on the bench.",
            security,
        )


def _inject(buffer, severity: str) -> None:
    from cv_argus import constants
    from cv_argus.alerts import Alert, AlertKind

    p_drowsy = constants.RULE_P_DROWSY[severity]
    drowsy = severity in ("medium", "critical")
    alert = Alert.new(
        AlertKind.DROWSINESS,
        level=2 if drowsy else 1,
        source_id="bt-test",
        payload={
            "severity": severity,
            "reason": "test injected by pi_bt_server.py",
            "class_name": "Drowsy" if drowsy else "Not Drowsy",
            "probabilities": [round(1 - p_drowsy, 2), p_drowsy],
            "transition": True,
        },
    )
    buffer.enqueue(alert)
    print(f"Queued a fake {severity} reading ({alert.id}). The ESP32 gets it on its next poll.")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--buffer", type=Path, default=DEFAULT_BUFFER,
                        help=f"the SQLite queue (default: {DEFAULT_BUFFER.relative_to(MODULE_ROOT)})")
    parser.add_argument("--channel", type=int, default=int(os.environ.get("BLUETOOTH_CHANNEL", "4")),
                        help="RFCOMM channel; must match the ESP32's PI_BT_RFCOMM_CHANNEL (default 4)")
    parser.add_argument("--security", default=os.environ.get("BLUETOOTH_SECURITY", "medium"),
                        choices=("low", "medium", "high"),
                        help="medium = paired + encrypted (default); low = any device, bench only")
    parser.add_argument("--allow", default=os.environ.get("ESP32_BT_MAC", ""),
                        help="only accept these ESP32 MACs (comma-separated)")
    parser.add_argument("--inject", choices=("none", "low", "medium", "critical"),
                        help="queue one fake reading with this severity and exit")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, datefmt="%H:%M:%S",
                        format="%(asctime)s bt-server %(levelname)s: %(message)s")
    _import_cv_argus()
    from cv_argus.buffer import open_buffer

    args.buffer.parent.mkdir(parents=True, exist_ok=True)
    try:
        buffer = open_buffer(args.buffer)
    except Exception as exc:  # sqlite3.OperationalError on a root-owned file, mostly
        logger.error("Can't open %s: %s. If Docker created it as root (rootful Docker), run "
                     "this with sudo or chown the data/ folder.", args.buffer, exc)
        return 1

    if args.inject:
        _inject(buffer, args.inject)
        buffer.close()
        return 0

    if not hasattr(socket, "AF_BLUETOOTH"):
        logger.error("This Python has no Bluetooth socket support (socket.AF_BLUETOOTH).")
        return 1

    from cv_argus.sender import BluetoothSppListener, SenderServer
    from cv_argus.sender.device_config import DeviceConfig

    _report_adapter(args.security)
    _load_env_files()
    try:
        device_config = DeviceConfig.from_env()
    except ValueError as exc:
        logger.error("%s", exc)
        return 1
    if device_config is None:
        logger.info("No device credentials: CONFIG gets NOCONFIG (fine for BT_LINK_TEST_MODE)")
    else:
        logger.info("Serving CONFIG for truck %s (%s)", device_config.truck_id,
                    device_config.backend_url)

    try:
        listener = BluetoothSppListener(channel=args.channel, security=args.security)
    except OSError as exc:
        logger.error("Can't listen on RFCOMM channel %d: %s. Is the adapter powered "
                     "(bluetoothctl power on), and is nothing else on that channel (a cv-argus "
                     "container with SENDER_TRANSPORT=bluetooth)?", args.channel, exc)
        return 1

    allowed = [mac.strip() for mac in args.allow.split(",") if mac.strip()] or None
    server = SenderServer(buffer, listener, device_config=device_config, allowed_peers=allowed)
    stop = threading.Event()
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    signal.signal(signal.SIGTERM, lambda *_: stop.set())

    server.start()
    logger.info("Listening on RFCOMM channel %d (security=%s), queue %s, %d unsent record(s). "
                "Waiting for the ESP32...", args.channel, args.security, args.buffer,
                buffer.unsent_count())
    while not stop.is_set() and server.is_alive:
        stop.wait(1.0)
    server.stop()
    server.join(timeout=5.0)
    buffer.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
