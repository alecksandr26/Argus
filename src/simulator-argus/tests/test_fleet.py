"""Provisioning against an in-memory fake backend (httpx.MockTransport): the simulator's guardian
(route guardian contracts G8) is found or created, fixed when needed, carried by every route, and
kept by the reset."""
from __future__ import annotations

import json
import re
from typing import Any

import httpx
import pytest

from simulator_argus.client import BackendClient
from simulator_argus.config import Settings
from simulator_argus.fleet import ensure_guardian, provision_fleet


class FakeBackend:
    """Just enough of backend-argus for `provision_fleet`: users, drivers, trucks, routes,
    alerts (always none) and rotate-key. Records every write."""

    def __init__(self, users: list[dict] | None = None) -> None:
        self.users: list[dict] = list(users or [])
        self.drivers: list[dict] = []
        self.trucks: list[dict] = []
        self.routes: list[dict] = []
        self.writes: list[tuple[str, str, Any]] = []
        self._next = 0

    def _id(self, prefix: str) -> str:
        self._next += 1
        return f"{prefix}-{self._next}"

    def handler(self, request: httpx.Request) -> httpx.Response:
        method, path = request.method, request.url.path
        body = json.loads(request.content) if request.content else None
        if method != "GET":
            self.writes.append((method, path, body))

        if path == "/api/auth/login":
            return httpx.Response(200, json={"access_token": "tok", "user": {}})
        if method == "GET":
            table = {
                "/api/users": self.users,
                "/api/drivers": self.drivers,
                "/api/trucks": self.trucks,
                "/api/routes": self.routes,
                "/api/alerts": [],
            }[path]
            return httpx.Response(200, json=table)
        if method == "POST" and path == "/api/users":
            user = {"id_user": self._id("u"), "last_login": None, **body}
            self.users.append(user)
            return httpx.Response(201, json=user)
        if method == "PUT" and path.startswith("/api/users/"):
            user = next(u for u in self.users if u["id_user"] == path.rsplit("/", 1)[1])
            user.update(body)
            return httpx.Response(200, json=user)
        if method == "POST" and path == "/api/drivers":
            driver = {"id_driver": self._id("d"), **body}
            self.drivers.append(driver)
            return httpx.Response(201, json=driver)
        if method == "POST" and path == "/api/trucks":
            truck = {"id_truck": self._id("t"), **body}
            self.trucks.append(truck)
            return httpx.Response(201, json=truck)
        if method == "POST" and path == "/api/routes":
            if not body.get("id_guardian"):
                return httpx.Response(422, json={"detail": "id_guardian is required"})
            route = {"id_route": self._id("r"), **body}
            self.routes.append(route)
            return httpx.Response(201, json=route)
        if method == "POST" and path.endswith("/rotate-key"):
            return httpx.Response(200, json={"device_api_key": "key", "id_truck": "x"})
        if method == "DELETE":
            kind, item_id = path.split("/")[2], path.rsplit("/", 1)[1]
            id_field = {"trucks": "id_truck", "drivers": "id_driver", "routes": "id_route",
                        "users": "id_user"}[kind]
            table = getattr(self, kind)
            table[:] = [x for x in table if x[id_field] != item_id]
            return httpx.Response(200, json={"detail": "deleted"})
        raise AssertionError(f"unexpected {method} {path}")


def _settings(**over: Any) -> Settings:
    base: dict[str, Any] = {
        "simulator_truck_count": 2,
        "simulator_use_osrm": False,
        "simulator_reset": True,
    }
    return Settings(_env_file=None, **{**base, **over})


def _client(fake: FakeBackend) -> BackendClient:
    return BackendClient("http://backend", transport=httpx.MockTransport(fake.handler))


def _guardian(**over: Any) -> dict:
    return {
        "id_user": "g-1",
        "email": "sim-guardian@argus.sim",
        "role": "guardian",
        "first_name": "SIM",
        "last_name": "Guardian",
        "phone_number": "+520000000000",
        "is_active": True,
        "last_login": None,
        **over,
    }


def _user_writes(fake: FakeBackend) -> list[tuple[str, str, Any]]:
    return [w for w in fake.writes if w[1].startswith("/api/users")]


async def test_creates_the_default_guardian_and_every_route_carries_it():
    fake = FakeBackend()
    async with _client(fake) as client:
        specs = await provision_fleet(client, _settings())

    assert len(specs) == 2
    [(method, path, body)] = _user_writes(fake)
    assert (method, path) == ("POST", "/api/users")
    assert body["email"] == "sim-guardian@argus.sim"
    assert body["role"] == "guardian"
    assert (body["first_name"], body["last_name"]) == ("SIM", "Guardian")
    assert body["phone_number"] == "+520000000000"
    assert body["is_active"] is True
    # A random password, sent as its SHA-256 digest like every password on the API boundary.
    assert re.fullmatch(r"[0-9a-f]{64}", body["password"])

    guardian_id = fake.users[0]["id_user"]
    assert [r["id_guardian"] for r in fake.routes] == [guardian_id, guardian_id]


async def test_an_existing_guardian_that_is_right_is_reused_without_writes():
    fake = FakeBackend([_guardian(email="SIM-Guardian@Argus.sim")])
    async with _client(fake) as client:
        guardian_id = await ensure_guardian(client, "tok", _settings())

    assert guardian_id == "g-1"
    assert _user_writes(fake) == []


async def test_an_inactive_guardian_with_a_bad_phone_is_reactivated_and_fixed():
    fake = FakeBackend([_guardian(is_active=False, phone_number="555-0101")])
    async with _client(fake) as client:
        await provision_fleet(client, _settings())

    [(method, path, body)] = _user_writes(fake)
    assert (method, path) == ("PUT", "/api/users/g-1")
    assert body == {"is_active": True, "phone_number": "+520000000000"}
    assert {r["id_guardian"] for r in fake.routes} == {"g-1"}


async def test_only_the_phone_is_fixed_on_an_active_guardian():
    fake = FakeBackend([_guardian(phone_number="+521111111111")])
    async with _client(fake) as client:
        await ensure_guardian(client, "tok", _settings())

    assert _user_writes(fake) == [("PUT", "/api/users/g-1", {"phone_number": "+520000000000"})]


async def test_custom_email_and_phone_are_normalised():
    fake = FakeBackend()
    settings = _settings(
        simulator_guardian_email="demo-guard@example.com",
        simulator_guardian_phone="+52 (33) 1234-5678",
    )
    async with _client(fake) as client:
        await ensure_guardian(client, "tok", settings)

    [(_, _, body)] = _user_writes(fake)
    assert body["email"] == "demo-guard@example.com"
    assert body["phone_number"] == "+523312345678"


async def test_an_email_that_belongs_to_another_role_is_never_converted():
    fake = FakeBackend([_guardian(role="admin")])
    async with _client(fake) as client:
        with pytest.raises(RuntimeError, match="admin account"):
            await provision_fleet(client, _settings())

    assert _user_writes(fake) == []
    assert fake.routes == []


async def test_reset_keeps_the_guardian_and_reuses_it():
    fake = FakeBackend()
    async with _client(fake) as client:
        await provision_fleet(client, _settings())
        first_guardian = fake.users[0]["id_user"]
        await provision_fleet(client, _settings())

    assert [u["id_user"] for u in fake.users] == [first_guardian]
    assert not any(m == "DELETE" and p.startswith("/api/users") for m, p, _ in fake.writes)
    # The reset removed the first run's SIM fleet; the new routes carry the same guardian.
    assert len(fake.routes) == 2
    assert {r["id_guardian"] for r in fake.routes} == {first_guardian}
