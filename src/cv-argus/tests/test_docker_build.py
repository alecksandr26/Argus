"""Docker build/run smoke tests — the opt-in tier.

Every test here is `@pytest.mark.docker` (via `pytestmark`), so it is:
  * deselected from a plain `pytest` run (see `pyproject.toml`'s `addopts`), and
  * skipped unless `docker` is on PATH *and* `CV_ARGUS_DOCKER_TESTS=1` (see `conftest.py`).

Run them with:

    CV_ARGUS_DOCKER_TESTS=1 pytest -m docker

The image build pulls ~1GB of TensorFlow plus the trained model artifacts and takes minutes;
the built image is shared across the module via a session-scoped fixture.
"""

import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.docker

MODULE_ROOT = Path(__file__).resolve().parents[1]
IMAGE_TAG = "cv-argus:pytest"

# The four artifacts the Dockerfile bakes in at build time (see constants.py).
EXPECTED_ARTIFACTS = [
    "cnn_face_crop_model.keras",
    "best_cnn_lstm_frozen_embedding.keras",
    "face_landmarker.task",
    "blaze_face_short_range.tflite",
]


def _docker(*args, **kwargs):
    return subprocess.run(
        ["docker", *args], capture_output=True, text=True, **kwargs
    )


def _compose_available() -> bool:
    return _docker("compose", "version").returncode == 0


@pytest.fixture(scope="session")
def built_image():
    result = _docker("build", "-t", IMAGE_TAG, ".", cwd=MODULE_ROOT, timeout=1800)
    if result.returncode != 0:
        pytest.fail(f"docker build failed:\n{result.stdout[-4000:]}\n{result.stderr[-4000:]}")
    return IMAGE_TAG


def _run_in_container(image, *cmd, timeout=120, env=None):
    args = ["run", "--rm"]
    for key, value in (env or {}).items():
        args += ["-e", f"{key}={value}"]
    return _docker(*args, image, *cmd, timeout=timeout)


# --- checks that don't need the image build ------------------------------------------------

def test_compose_config_parses():
    if not _compose_available():
        pytest.skip("docker compose plugin not available")
    result = _docker("compose", "-f", "docker-compose.yml", "config", cwd=MODULE_ROOT)
    assert result.returncode == 0, result.stderr
    assert "cv-argus" in result.stdout


def test_compose_pi_overlay_config_parses():
    if not _compose_available():
        pytest.skip("docker compose plugin not available")
    result = _docker(
        "compose", "-f", "docker-compose.yml", "-f", "docker-compose.pi.yml", "config",
        cwd=MODULE_ROOT,
    )
    assert result.returncode == 0, result.stderr
    # the overlay flips the frame source to the Pi CSI camera
    assert "picamera" in result.stdout


# --- checks against the built image -------------------------------------------------------

def test_image_builds(built_image):
    result = _docker("image", "inspect", built_image)
    assert result.returncode == 0


def test_cv_argus_package_imports_in_container(built_image):
    result = _run_in_container(
        built_image, "python", "-c",
        "import cv_argus, cv_argus.model, cv_argus.pipeline, cv_argus.main; print('ok')",
    )
    assert result.returncode == 0, result.stderr
    assert "ok" in result.stdout


def test_model_artifacts_are_baked_in(built_image):
    result = _run_in_container(built_image, "ls", "/app/models")
    assert result.returncode == 0, result.stderr
    for artifact in EXPECTED_ARTIFACTS:
        assert artifact in result.stdout, f"{artifact} missing from /app/models"


def test_downloader_is_a_fast_noop_when_cached(built_image):
    # Re-running the build-time download step must hit the cache, not the network.
    result = _run_in_container(
        built_image, "python", "-m", "cv_argus.model.downloader", timeout=60,
    )
    assert result.returncode == 0, result.stderr
    assert "already cached" in (result.stdout + result.stderr)


def test_pipeline_bundle_downloader_is_a_fast_noop_when_cached(built_image):
    result = _run_in_container(
        built_image, "python", "-m", "cv_argus.pipeline.downloader", timeout=60,
    )
    assert result.returncode == 0, result.stderr
    assert "already cached" in (result.stdout + result.stderr)


def test_entrypoint_passes_through_to_cmd(built_image):
    result = _run_in_container(built_image, "echo", "entrypoint-ok")
    assert result.returncode == 0
    assert result.stdout.strip() == "entrypoint-ok"


def test_unknown_source_is_rejected_in_container(built_image):
    # Exercise the SOURCE guard without loading the real models: call the helper directly.
    result = _run_in_container(
        built_image, "python", "-c",
        "from cv_argus.main import _build_source; _build_source()",
        env={"SOURCE": "not-a-real-source"},
    )
    assert result.returncode != 0
    assert "Unknown SOURCE" in (result.stdout + result.stderr)


def test_unknown_output_is_rejected_in_container(built_image):
    result = _run_in_container(
        built_image, "python", "-c",
        "from cv_argus.main import _build_outputs; _build_outputs()",
        env={"OUTPUTS": "logging,carrier-pigeon"},
    )
    assert result.returncode != 0
    assert "Unknown output" in (result.stdout + result.stderr)
