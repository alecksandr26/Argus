"""`DeviceConfig` — this truck's backend credentials, which the Pi holds and the ESP32 fetches
over Bluetooth (`CONFIG`, see `protocol.py`).

The Pi never calls the backend itself: the ESP32 is the device that does (root `CLAUDE.md`).
The credentials still live here because the Pi is the provisioned, re-deployable half of the
truck: the dashboard's Fleet screen issues the key and hands it out as an `argus-device.env`
file, which `docker-compose.yml` loads into this container. That keeps the ESP32 firmware
generic, with no per-truck key flashed into it.

The ESP32's network settings travel the same way, so nothing site-specific is flashed into the
firmware either: the WiFi it falls back to (`ARGUS_WIFI_SSID`/`ARGUS_WIFI_PASSWORD`) and the
SIM's mobile-data APN (`ARGUS_GPRS_APN`/`ARGUS_GPRS_USER`/`ARGUS_GPRS_PASSWORD`; no APN = the
ESP32 doesn't use mobile data). They're optional and set per installation in this container's
`.env`, not in `argus-device.env`, which the dashboard regenerates on every key rotation. Unlike
the credentials they're not `.strip()`ped: an SSID may really end in a space.

Two more optional values come from `argus-device.env` itself, for the ESP32's critical-alert SMS
(`docs/designs/grip-alarm-contracts.md` C1): the truck's plate (`ARGUS_TRUCK_PLATE`), which the
SMS names, and the phone it goes to (`ARGUS_ALERT_PHONE`, E.164 like `+523312345678`; unset =
no SMS). Both are stripped and both stay outside the "all three or none" credential check. A
phone that isn't E.164 is logged and left out rather than raised: a typo in it must not take the
truck's alerts offline.

The key and passwords are secrets: `repr()` masks them, and nothing in this module logs them.
The phone is personal data: `repr()` and the log show it masked (`mask_phone`), never in full.
"""

from __future__ import annotations

import logging
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field, fields

TRUCK_ID_ENV = "ARGUS_TRUCK_ID"
DEVICE_API_KEY_ENV = "ARGUS_DEVICE_API_KEY"
BACKEND_URL_ENV = "ARGUS_BACKEND_URL"
_ENV_VARS = (TRUCK_ID_ENV, DEVICE_API_KEY_ENV, BACKEND_URL_ENV)

# Optional, from argus-device.env, outside the credential check above.
TRUCK_PLATE_ENV = "ARGUS_TRUCK_PLATE"
ALERT_PHONE_ENV = "ARGUS_ALERT_PHONE"
# E.164, the same regex the backend, the dashboard and the firmware check (contract C1).
E164_PHONE = re.compile(r"^\+[1-9][0-9]{7,14}$")

# Optional network settings: wire field name -> env var.
NETWORK_ENV_VARS = {
    "wifi_ssid": "ARGUS_WIFI_SSID",
    "wifi_password": "ARGUS_WIFI_PASSWORD",
    "gprs_apn": "ARGUS_GPRS_APN",
    "gprs_user": "ARGUS_GPRS_USER",
    "gprs_password": "ARGUS_GPRS_PASSWORD",
}

logger = logging.getLogger(__name__)


def mask_phone(phone: str) -> str:
    """`+523312345678` -> `+52******5678`: the last 4 digits (and an E.164 number's `+` and first
    two digits) stay, enough to tell which phone is set without printing it."""
    keep_start = 3 if E164_PHONE.match(phone) else 0
    if len(phone) <= keep_start + 4:
        return "*" * len(phone)
    return phone[:keep_start] + "*" * (len(phone) - keep_start - 4) + phone[-4:]


@dataclass(frozen=True)
class DeviceConfig:
    truck_id: str
    device_api_key: str = field(repr=False)
    backend_url: str
    wifi_ssid: str | None = None
    wifi_password: str | None = field(default=None, repr=False)
    gprs_apn: str | None = None
    gprs_user: str | None = None
    gprs_password: str | None = field(default=None, repr=False)
    truck_plate: str | None = None
    # Masked by __repr__ below rather than hidden: which phone is set is worth seeing.
    alert_phone: str | None = field(default=None, repr=False)

    def __repr__(self) -> str:
        # The dataclass repr (secrets left out by `repr=False`), plus the phone masked.
        shown = [f"{f.name}={getattr(self, f.name)!r}" for f in fields(self) if f.repr]
        if self.alert_phone is not None:
            shown.append(f"alert_phone={mask_phone(self.alert_phone)!r}")
        return f"{type(self).__name__}({', '.join(shown)})"

    def to_wire(self) -> dict[str, str]:
        """The `CONFIG` response payload. Field names match the ESP32's `device_config.cpp`;
        unset network settings are left out (the ESP32 then has no WiFi / no mobile data), and
        so are an unset plate and phone (no SMS; an older ESP32 ignores both fields anyway)."""
        payload = {
            "truck_id": self.truck_id,
            "device_api_key": self.device_api_key,
            "backend_url": self.backend_url,
        }
        for name in (*NETWORK_ENV_VARS, "truck_plate", "alert_phone"):
            value = getattr(self, name)
            if value is not None:
                payload[name] = value
        return payload

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> DeviceConfig | None:
        """Reads the three `ARGUS_*` variables. `None` when none is set (an unprovisioned truck:
        the ESP32 gets `NOCONFIG`). Raises `ValueError` when only some are set, since a partial
        config is a provisioning mistake that should fail at startup, not on the road. The plate
        and phone never raise: an invalid phone is logged (masked) and left out."""
        env = os.environ if environ is None else environ
        values = {name: env.get(name, "").strip() for name in _ENV_VARS}
        if not any(values.values()):
            return None
        missing = [name for name, value in values.items() if not value]
        if missing:
            raise ValueError(
                f"Incomplete device credentials: {', '.join(missing)} not set. Download a "
                "fresh argus-device.env from the dashboard's Fleet screen."
            )
        # An empty value means unset; anything else is kept verbatim, spaces included.
        network = {name: env.get(var) or None for name, var in NETWORK_ENV_VARS.items()}
        return cls(
            truck_id=values[TRUCK_ID_ENV],
            device_api_key=values[DEVICE_API_KEY_ENV],
            backend_url=values[BACKEND_URL_ENV].rstrip("/"),
            **network,
            truck_plate=env.get(TRUCK_PLATE_ENV, "").strip() or None,
            alert_phone=_alert_phone(env.get(ALERT_PHONE_ENV, "").strip()),
        )


def _alert_phone(phone: str) -> str | None:
    """The SMS phone, or `None` when unset or not E.164 (warned, masked: no SMS until fixed)."""
    if not phone:
        return None
    if not E164_PHONE.match(phone):
        logger.warning(
            "Ignoring %s=%s: not an E.164 number (+, then 8 to 15 digits) -- the ESP32 sends no "
            "critical-alert SMS until it's fixed in argus-device.env",
            ALERT_PHONE_ENV,
            mask_phone(phone),
        )
        return None
    return phone
