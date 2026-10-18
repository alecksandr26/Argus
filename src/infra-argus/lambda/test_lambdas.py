"""Unit tests for the reaper (R5) and kill switch (R8) Lambdas, with stubbed boto3 clients.

Run: python -m pytest src/infra-argus/lambda (needs only pytest + boto3).
"""
import importlib
import os
import sys
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.dirname(__file__))


class FakeEcs:
    def __init__(self, tasks):
        self.tasks = tasks  # arn -> createdAt
        self.stopped = []

    def list_tasks(self, cluster, startedBy, desiredStatus):
        assert startedBy == "argus-simulator" and desiredStatus == "RUNNING"
        return {"taskArns": [a for a in self.tasks if a not in self.stopped]}

    def describe_tasks(self, cluster, tasks):
        return {"tasks": [{"taskArn": a, "createdAt": self.tasks[a]} for a in tasks]}

    def stop_task(self, cluster, task, reason):
        self.stopped.append(task)


class FakeLambda:
    def __init__(self):
        self.calls = []

    def put_function_concurrency(self, FunctionName, ReservedConcurrentExecutions):
        self.calls.append((FunctionName, ReservedConcurrentExecutions))


@pytest.fixture
def env(monkeypatch):
    # Hermetic: never touch the developer's real AWS profile (e.g. an `aws login` session).
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_CONFIG_FILE", os.devnull)
    monkeypatch.setenv("AWS_SHARED_CREDENTIALS_FILE", os.devnull)
    monkeypatch.delenv("AWS_PROFILE", raising=False)
    monkeypatch.setenv("CLUSTER", "argus")
    monkeypatch.setenv("STARTED_BY", "argus-simulator")
    monkeypatch.setenv("MAX_MINUTES", "10")
    monkeypatch.setenv("BACKEND_FUNCTION", "argus-backend")


def test_reaper_stops_only_tasks_older_than_one_session(env, monkeypatch):
    reaper = importlib.import_module("reaper")
    importlib.reload(reaper)
    now = datetime.now(timezone.utc)
    fake = FakeEcs({"old": now - timedelta(minutes=11), "new": now - timedelta(minutes=3)})
    monkeypatch.setattr(reaper, "ecs", fake)

    assert reaper.handler({}, None) == {"stopped": ["old"]}
    assert fake.stopped == ["old"]


def test_reaper_noop_when_nothing_runs(env, monkeypatch):
    reaper = importlib.import_module("reaper")
    importlib.reload(reaper)
    monkeypatch.setattr(reaper, "ecs", FakeEcs({}))
    assert reaper.handler({}, None) == {"stopped": []}


def test_kill_switch_throttles_backend_and_stops_tasks(env, monkeypatch):
    kill = importlib.import_module("kill_switch")
    importlib.reload(kill)
    fake_ecs = FakeEcs({"t1": datetime.now(timezone.utc)})
    fake_lambda = FakeLambda()
    monkeypatch.setattr(kill, "ecs", fake_ecs)
    monkeypatch.setattr(kill, "lam", fake_lambda)

    kill.handler({}, None)
    assert fake_lambda.calls == [("argus-backend", 0)]
    assert fake_ecs.stopped == ["t1"]
