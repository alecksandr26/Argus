"""Settings that changed name or default with the combined report."""
from __future__ import annotations

import pydantic
import pytest

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


def _blip_settings(monkeypatch, **env: str) -> Settings:
    for name in ("SIMULATOR_NORMAL_BLIP_CHANCE", "SIMULATOR_MEDIUM_BLIP_PROBABILITY"):
        monkeypatch.delenv(name, raising=False)
    return _settings(monkeypatch, **env)


def test_normal_blip_chance_defaults_to_one_route_in_two(monkeypatch):
    settings = _blip_settings(monkeypatch)
    assert settings.simulator_normal_blip_chance == 0.5
    assert settings.simulator_medium_blip_probability is None


def test_normal_blip_chance_reads_its_env_name(monkeypatch):
    settings = _blip_settings(monkeypatch, SIMULATOR_NORMAL_BLIP_CHANCE="0.2")
    assert settings.simulator_normal_blip_chance == 0.2


def test_normal_blip_chance_must_be_a_probability(monkeypatch):
    with pytest.raises(pydantic.ValidationError):
        _blip_settings(monkeypatch, SIMULATOR_NORMAL_BLIP_CHANCE="1.5")


def test_the_retired_per_5_s_blip_rate_is_ignored_with_a_warning(monkeypatch, caplog):
    from simulator_argus.__main__ import warn_retired_settings

    settings = _blip_settings(monkeypatch, SIMULATOR_MEDIUM_BLIP_PROBABILITY="0.1")
    # Read, but it changes nothing: a per-5-s rate has no per-route meaning.
    assert settings.simulator_normal_blip_chance == 0.5
    with caplog.at_level("WARNING"):
        warnings = warn_retired_settings(settings)
    assert len(warnings) == 1 and "SIMULATOR_MEDIUM_BLIP_PROBABILITY" in warnings[0]
    assert "SIMULATOR_NORMAL_BLIP_CHANCE" in caplog.text

    assert warn_retired_settings(_blip_settings(monkeypatch)) == []
