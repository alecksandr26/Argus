"""`pipeline/downloader.py` — the public, unauthenticated HTTP fetch for the two MediaPipe
bundles. Hermetic: `urllib.request.urlretrieve` is monkeypatched everywhere.
"""

import urllib.request

import pytest

from cv_argus import constants
from cv_argus.pipeline import downloader
from cv_argus.pipeline.downloader import (
    BundleDownloadError,
    _download_bundle,
    download_face_detector_bundle,
    download_face_landmarker_bundle,
)


@pytest.fixture
def fake_urlretrieve(monkeypatch):
    calls = []

    def _fake(url, destination):
        calls.append({"url": url, "destination": str(destination)})
        from pathlib import Path

        Path(destination).write_bytes(b"stub-bundle-bytes")

    monkeypatch.setattr(urllib.request, "urlretrieve", _fake)
    return calls


def test_cache_hit_skips_download(tmp_path, fake_urlretrieve):
    cached = tmp_path / constants.FACE_LANDMARKER_BUNDLE_FILENAME
    cached.write_bytes(b"already here")

    result = download_face_landmarker_bundle(model_dir=tmp_path)

    assert result == cached
    assert fake_urlretrieve == []


def test_downloads_landmarker_bundle_when_absent(tmp_path, fake_urlretrieve):
    result = download_face_landmarker_bundle(model_dir=tmp_path)

    assert result == tmp_path / constants.FACE_LANDMARKER_BUNDLE_FILENAME
    assert result.exists()
    assert fake_urlretrieve[0]["url"] == constants.FACE_LANDMARKER_BUNDLE_URL


def test_downloads_detector_bundle_when_absent(tmp_path, fake_urlretrieve):
    result = download_face_detector_bundle(model_dir=tmp_path)

    assert result == tmp_path / constants.FACE_DETECTOR_BUNDLE_FILENAME
    assert fake_urlretrieve[0]["url"] == constants.FACE_DETECTOR_BUNDLE_URL


def test_download_failure_is_wrapped(tmp_path, monkeypatch):
    def _boom(url, destination):
        raise OSError("network down")

    monkeypatch.setattr(urllib.request, "urlretrieve", _boom)
    with pytest.raises(BundleDownloadError, match="network down"):
        _download_bundle("https://example/x.task", tmp_path, "x.task", label="Face bundle")


def test_url_env_var_overrides_constant(tmp_path, fake_urlretrieve, monkeypatch):
    monkeypatch.setenv("FACE_DETECTOR_BUNDLE_URL", "https://example.test/blaze.tflite")
    download_face_detector_bundle(model_dir=tmp_path)
    assert fake_urlretrieve[0]["url"] == "https://example.test/blaze.tflite"


def test_empty_env_var_is_treated_as_absent(tmp_path, fake_urlretrieve, monkeypatch):
    monkeypatch.setenv("FACE_LANDMARKER_BUNDLE_URL", "")
    download_face_landmarker_bundle(model_dir=tmp_path)
    assert fake_urlretrieve[0]["url"] == constants.FACE_LANDMARKER_BUNDLE_URL


def test_model_dir_env_var_is_honoured(tmp_path, fake_urlretrieve, monkeypatch):
    monkeypatch.setenv("MODEL_DIR", str(tmp_path))
    result = download_face_detector_bundle()
    assert result.parent == tmp_path
