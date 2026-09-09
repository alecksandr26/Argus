"""Shared test setup for the `cv-argus` suite.

Two jobs:

1. **Make `cv_argus` importable even without `pip install -e .`.** The package lives on disk
   as `src/` and is only named `cv_argus` through `setup.py`'s `package_dir` remap (see the
   repo's `pyproject.toml`/`setup.py` and `CLAUDE.md`'s "Python packaging" section). If the
   editable install is present (the normal case, and what the Dockerfile does) we use it
   as-is; if it isn't, we register `src/` as the `cv_argus` package by hand so `pytest` still
   works from a bare checkout.

2. **Gate the opt-in `docker` marker.** `pyproject.toml` already deselects `-m docker` from a
   plain run; on top of that, a `docker` test skips itself unless `docker` is on `PATH` *and*
   `CV_ARGUS_DOCKER_TESTS=1` is set, because building the image pulls ~1GB of TensorFlow and
   the trained model artifacts and takes minutes.

Nothing in the default (non-`docker`) suite touches the network, a camera, or Google Drive.
"""

import importlib.util
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

_TESTS_DIR = Path(__file__).resolve().parent
_MODULE_ROOT = _TESTS_DIR.parent          # src/cv-argus/
_SRC_DIR = _MODULE_ROOT / "src"           # the on-disk home of the `cv_argus` package


def _ensure_cv_argus_importable() -> None:
    if importlib.util.find_spec("cv_argus") is not None:
        return  # `pip install -e .` already did the work
    # Bare checkout: bind the package name `cv_argus` to the `src/` directory, mirroring what
    # `setup.py`'s `package_dir={"cv_argus": "src"}` does at install time. `submodule_search_
    # locations` is what lets `import cv_argus.model` / `cv_argus.pipeline` resolve afterwards.
    spec = importlib.util.spec_from_file_location(
        "cv_argus",
        _SRC_DIR / "__init__.py",
        submodule_search_locations=[str(_SRC_DIR)],
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules["cv_argus"] = module
    spec.loader.exec_module(module)


_ensure_cv_argus_importable()


def pytest_runtest_setup(item: pytest.Item) -> None:
    """Skip `@pytest.mark.docker` tests unless the environment is set up to run them:
    `docker` on PATH, `CV_ARGUS_DOCKER_TESTS=1`, and a reachable daemon (a CLI-only install,
    or one where the user isn't in the `docker` group, can't build or run)."""
    if "docker" not in item.keywords:
        return
    if shutil.which("docker") is None:
        pytest.skip("docker is not on PATH")
    if os.environ.get("CV_ARGUS_DOCKER_TESTS") != "1":
        pytest.skip("set CV_ARGUS_DOCKER_TESTS=1 to run the Docker build/run tests")
    try:
        reachable = subprocess.run(
            ["docker", "info"], capture_output=True, timeout=30
        ).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        reachable = False
    if not reachable:
        pytest.skip("docker daemon not reachable (need daemon access, e.g. the `docker` group)")


@pytest.fixture
def module_root() -> Path:
    """Absolute path to `src/cv-argus/` — the directory holding the Dockerfile, compose files,
    and `src/`."""
    return _MODULE_ROOT
