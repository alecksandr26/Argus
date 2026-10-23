"""Settings that changed name or default with the combined report."""
from __future__ import annotations

from simulator_argus.config import Settings


def _settings(monkeypatch, **env: str) -> Settings:
    for name in (
        "SIMULATOR_REPORT_INTERVAL_S",
        "SIMULATOR_STATUS_INTERVAL_SECONDS",
        "SIMULATOR_SCENARIO_WEIGHTS",
    ):
        monkeypatch.delenv(name, raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    return Settings(_env_file=None)


def test_report_interval_defaults_to_5_s(monkeypatch):
    assert _settings(monkeypatch).simulator_report_interval_s == 5.0


def test_report_interval_reads_its_own_name(monkeypatch):
    settings = _settings(monkeypatch, SIMULATOR_REPORT_INTERVAL_S="3")
    assert settings.simulator_report_interval_s == 3.0


def test_report_interval_still_reads_the_old_status_interval_name(monkeypatch):
    settings = _settings(monkeypatch, SIMULATOR_STATUS_INTERVAL_SECONDS="7")
    assert settings.simulator_report_interval_s == 7.0


def test_default_weights_have_no_panic_profile(monkeypatch):
    weights = _settings(monkeypatch).simulator_scenario_weights
    assert "panic" not in weights
    assert "no_face" in weights
