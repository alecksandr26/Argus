"""`model/downloader.py` — the gdown-if-not-cached fetch for the two trained `.keras` artifacts.

Hermetic: `gdown.download` is monkeypatched in every test. The cache-hit path (the common
case on a rebuilt container, and the reason artifacts are baked in at image-build time) must
not call it at all.
"""

import pytest

from cv_argus import constants
from cv_argus.model import downloader
from cv_argus.model.downloader import (
    ModelDownloadError,
    _download_from_drive,
    download_cnn_model,
    download_fused_model,
)


@pytest.fixture
def fake_gdown(monkeypatch):
    """Replace `gdown.download` with a recorder that writes a stub file and reports success.
    Returns the call-record list so a test can assert on args (or on it staying empty)."""
    calls = []

    def _fake(*, id, output, quiet):  # matches downloader.py's keyword call
        calls.append({"id": id, "output": output, "quiet": quiet})
        from pathlib import Path

        Path(output).write_bytes(b"stub-keras-bytes")
        return output

    monkeypatch.setattr(downloader.gdown, "download", _fake)
    return calls


def test_cache_hit_returns_without_downloading(tmp_path, fake_gdown):
    cached = tmp_path / constants.CNN_MODEL_FILENAME
    cached.write_bytes(b"already here")

    result = download_cnn_model(model_dir=tmp_path)

    assert result == cached
    assert fake_gdown == []  # never touched the network


def test_download_when_absent_calls_gdown_once(tmp_path, fake_gdown):
    result = download_fused_model(file_id="abc123", model_dir=tmp_path)

    assert result == tmp_path / constants.FUSED_MODEL_FILENAME
    assert result.exists()
    assert len(fake_gdown) == 1
    assert fake_gdown[0]["id"] == "abc123"


def test_missing_file_id_and_no_cache_raises(tmp_path):
    with pytest.raises(ModelDownloadError, match="no Drive file id"):
        _download_from_drive(None, tmp_path, "model.keras", label="CNN model")


def test_gdown_returning_none_raises(tmp_path, monkeypatch):
    monkeypatch.setattr(downloader.gdown, "download", lambda **kw: None)
    with pytest.raises(ModelDownloadError, match="not created"):
        _download_from_drive("id", tmp_path, "model.keras", label="CNN model")


def test_gdown_exception_is_wrapped(tmp_path, monkeypatch):
    def _boom(**kw):
        raise RuntimeError("drive said no")

    monkeypatch.setattr(downloader.gdown, "download", _boom)
    with pytest.raises(ModelDownloadError, match="drive said no"):
        _download_from_drive("id", tmp_path, "model.keras", label="CNN model")


def test_explicit_arg_beats_env_and_constant(tmp_path, fake_gdown, monkeypatch):
    monkeypatch.setenv("CNN_MODEL_DRIVE_FILE_ID", "from-env")
    download_cnn_model(file_id="from-arg", model_dir=tmp_path)
    assert fake_gdown[0]["id"] == "from-arg"


def test_env_var_beats_constant(tmp_path, fake_gdown, monkeypatch):
    monkeypatch.setenv("FUSED_MODEL_DRIVE_FILE_ID", "env-fused-id")
    download_fused_model(model_dir=tmp_path)
    assert fake_gdown[0]["id"] == "env-fused-id"


def test_empty_env_var_is_treated_as_absent(tmp_path, fake_gdown, monkeypatch):
    monkeypatch.setenv("CNN_MODEL_DRIVE_FILE_ID", "")  # e.g. an unset Docker build ARG
    download_cnn_model(model_dir=tmp_path)
    assert fake_gdown[0]["id"] == constants.CNN_MODEL_DRIVE_FILE_ID


def test_model_dir_env_var_is_honoured(tmp_path, fake_gdown, monkeypatch):
    monkeypatch.setenv("MODEL_DIR", str(tmp_path))
    result = download_cnn_model(file_id="x")
    assert result.parent == tmp_path
