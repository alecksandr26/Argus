"""root_admin-only cloud simulator on/off control — app/simulator_control.py.

ECS is replaced by `FakeEcs`, an in-memory stand-in for the four boto3 calls the controller
makes, so these stay hermetic (no AWS credentials, no network), like the rest of the default
tier.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.config import settings
from app.main import app
from app.models.simulator_session import SimulatorSession
from app.simulator_control import STARTED_BY, SimulatorController, get_simulator_controller
from tests.conftest import auth_headers


class FakeEcs:
    def __init__(self) -> None:
        self.tasks: dict[str, dict] = {}
        self.run_calls: list[dict] = []
        self.stopped: list[str] = []
        self._n = 0

    def run_task(self, **kwargs):
        self.run_calls.append(kwargs)
        self._n += 1
        arn = f"arn:aws:ecs:us-east-1:000000000000:task/argus/{self._n}"
        self.tasks[arn] = {"taskArn": arn, "lastStatus": "PROVISIONING", "desired": "RUNNING"}
        return {"tasks": [self.tasks[arn]], "failures": []}

    def list_tasks(self, *, cluster, startedBy, desiredStatus):
        assert startedBy == STARTED_BY
        return {
            "taskArns": [a for a, t in self.tasks.items() if t["desired"] == desiredStatus]
        }

    def describe_tasks(self, *, cluster, tasks):
        return {"tasks": [self.tasks[a] for a in tasks if a in self.tasks]}

    def stop_task(self, *, cluster, task, reason):
        self.stopped.append(task)
        self.tasks[task].update(lastStatus="STOPPED", desired="STOPPED")
        return {}

    def set_status(self, arn: str, status: str) -> None:
        self.tasks[arn]["lastStatus"] = status


@pytest.fixture
def ecs(monkeypatch):
    fake = FakeEcs()
    monkeypatch.setattr(settings, "simulator_control", "ecs")
    monkeypatch.setattr(settings, "simulator_ecs_cluster", "argus")
    monkeypatch.setattr(settings, "simulator_task_definition", "argus-simulator")
    monkeypatch.setattr(settings, "simulator_subnets", "subnet-a,subnet-b")
    monkeypatch.setattr(settings, "simulator_security_groups", "sg-1")
    monkeypatch.setattr(settings, "simulator_backend_url", "https://api.example")
    monkeypatch.setattr(settings, "simulator_session_minutes", 10)
    monkeypatch.setattr(settings, "simulator_monthly_quota_minutes", 120)
    app.dependency_overrides[get_simulator_controller] = lambda: SimulatorController(fake)
    yield fake
    app.dependency_overrides.pop(get_simulator_controller, None)


async def test_requires_root_admin(api_client, ecs, admin_user, guardian, truck_driver_user):
    for path, method in (("", "get"), ("/start", "post"), ("/stop", "post")):
        resp = await getattr(api_client, method)(f"/api/simulator{path}")
        assert resp.status_code == 401
        for user in (admin_user, guardian, truck_driver_user):
            resp = await getattr(api_client, method)(
                f"/api/simulator{path}", headers=auth_headers(user)
            )
            assert resp.status_code == 403, (path, user.role)
    assert ecs.run_calls == []


async def test_disabled_by_default_returns_503(api_client, root_admin, admin_user):
    assert settings.simulator_control == "disabled"
    resp = await api_client.get("/api/simulator", headers=auth_headers(root_admin))
    assert resp.status_code == 503
    # The role check still wins over "not configured".
    resp = await api_client.post("/api/simulator/start", headers=auth_headers(admin_user))
    assert resp.status_code == 403


async def test_status_when_off(api_client, ecs, root_admin):
    resp = await api_client.get("/api/simulator", headers=auth_headers(root_admin))
    assert resp.status_code == 200
    body = resp.json()
    assert body["state"] == "stopped"
    assert body["session_minutes"] == 10
    assert body["quota_remaining_minutes"] == 120
    assert body["sessions_remaining"] == 12


async def test_start_runs_a_fixed_ten_minute_spot_task(api_client, ecs, root_admin):
    resp = await api_client.post(
        "/api/simulator/start", json={"truck_count": 4}, headers=auth_headers(root_admin)
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["state"] == "starting"
    started = datetime.fromisoformat(body["started_at"])
    auto_off = datetime.fromisoformat(body["auto_off_at"])
    assert auto_off - started == timedelta(minutes=10)
    assert body["quota_used_minutes"] == 10
    assert body["sessions_remaining"] == 11

    (call,) = ecs.run_calls
    assert call["startedBy"] == STARTED_BY
    assert call["capacityProviderStrategy"] == [{"capacityProvider": "FARGATE_SPOT", "weight": 1}]
    assert call["networkConfiguration"]["awsvpcConfiguration"]["subnets"] == ["subnet-a", "subnet-b"]
    (override,) = call["overrides"]["containerOverrides"]
    env = {e["name"]: e["value"] for e in override["environment"]}
    assert env["SIMULATOR_SESSION_MINUTES"] == "10"
    assert env["SIMULATOR_TRUCK_COUNT"] == "4"
    assert env["BACKEND_BASE_URL"] == "https://api.example"


async def test_backend_url_defaults_to_the_request_url(api_client, ecs, root_admin, monkeypatch):
    monkeypatch.setattr(settings, "simulator_backend_url", "")
    await api_client.post("/api/simulator/start", headers=auth_headers(root_admin))
    (override,) = ecs.run_calls[0]["overrides"]["containerOverrides"]
    env = {e["name"]: e["value"] for e in override["environment"]}
    assert env["BACKEND_BASE_URL"] == "http://test"


async def test_duration_cannot_be_requested(api_client, ecs, root_admin):
    resp = await api_client.post(
        "/api/simulator/start", json={"minutes": 60}, headers=auth_headers(root_admin)
    )
    assert resp.status_code == 422
    resp = await api_client.post(
        "/api/simulator/start", json={"truck_count": 50}, headers=auth_headers(root_admin)
    )
    assert resp.status_code == 422
    assert ecs.run_calls == []


async def test_only_one_at_a_time(api_client, ecs, root_admin):
    headers = auth_headers(root_admin)
    assert (await api_client.post("/api/simulator/start", headers=headers)).status_code == 200
    ecs.set_status(next(iter(ecs.tasks)), "RUNNING")
    resp = await api_client.get("/api/simulator", headers=headers)
    assert resp.json()["state"] == "running"
    assert (await api_client.post("/api/simulator/start", headers=headers)).status_code == 409
    assert len(ecs.run_calls) == 1


async def test_orphan_ecs_task_also_blocks_start(api_client, ecs, root_admin):
    # A task ECS still runs but Mongo has no open session for (e.g. started by hand).
    ecs.tasks["arn:orphan"] = {"taskArn": "arn:orphan", "lastStatus": "RUNNING", "desired": "RUNNING"}
    resp = await api_client.post("/api/simulator/start", headers=auth_headers(root_admin))
    assert resp.status_code == 409


async def test_stop_turns_it_off(api_client, ecs, root_admin):
    headers = auth_headers(root_admin)
    await api_client.post("/api/simulator/start", headers=headers)
    arn = next(iter(ecs.tasks))
    resp = await api_client.post("/api/simulator/stop", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["state"] == "stopped"
    assert ecs.stopped == [arn]
    session = await SimulatorSession.find_one()
    assert session.stop_reason == "stopped_by_user"
    # Not refunded.
    assert resp.json()["quota_used_minutes"] == 10


async def test_session_past_ten_minutes_is_stopped_on_status(api_client, ecs, root_admin):
    headers = auth_headers(root_admin)
    await api_client.post("/api/simulator/start", headers=headers)
    arn = next(iter(ecs.tasks))
    ecs.set_status(arn, "RUNNING")
    session = await SimulatorSession.find_one()
    session.started_at = datetime.now(timezone.utc) - timedelta(minutes=11)
    await session.save()

    resp = await api_client.get("/api/simulator", headers=headers)
    assert resp.json()["state"] == "stopped"
    assert ecs.stopped == [arn]
    assert (await SimulatorSession.find_one()).stop_reason == "auto_off"


async def test_task_that_ended_by_itself_closes_the_session(api_client, ecs, root_admin):
    headers = auth_headers(root_admin)
    await api_client.post("/api/simulator/start", headers=headers)
    ecs.set_status(next(iter(ecs.tasks)), "STOPPED")
    resp = await api_client.get("/api/simulator", headers=headers)
    assert resp.json()["state"] == "stopped"
    assert (await SimulatorSession.find_one()).stop_reason == "task_ended"


async def test_monthly_quota_blocks_start(api_client, ecs, root_admin):
    now = datetime.now(timezone.utc)
    for _ in range(12):
        await SimulatorSession(
            started_by=str(root_admin.id),
            started_at=now,
            requested_minutes=10,
            truck_count=6,
            task_arn="arn:old",
            stopped_at=now,
            stop_reason="auto_off",
        ).insert()
    resp = await api_client.post("/api/simulator/start", headers=auth_headers(root_admin))
    assert resp.status_code == 429
    assert ecs.run_calls == []


async def test_quota_resets_each_month(api_client, ecs, root_admin):
    last_month = datetime.now(timezone.utc).replace(day=1) - timedelta(days=1)
    for _ in range(12):
        await SimulatorSession(
            started_by=str(root_admin.id),
            started_at=last_month,
            requested_minutes=10,
            truck_count=6,
            task_arn="arn:old",
            stopped_at=last_month,
            stop_reason="auto_off",
        ).insert()
    resp = await api_client.post("/api/simulator/start", headers=auth_headers(root_admin))
    assert resp.status_code == 200
    assert resp.json()["quota_used_minutes"] == 10
