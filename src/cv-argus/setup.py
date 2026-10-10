from setuptools import setup

# `package_dir={"cv_argus": "src"}` maps the *root* package name `cv_argus` onto the `src/`
# directory itself — the on-disk folder stays named `src` (no nested src/cv_argus/ wrapper),
# but everything under it becomes importable as `cv_argus.*`.
#
# Each entry below must be added by hand as a subpackage is created under src/ (e.g. once
# src/model/__init__.py exists, add "cv_argus.model" here) — this list isn't auto-discovered.
setup(
    name="cv_argus",
    version="0.1.0",
    description=(
        "Argus edge computer-vision/AI module: face-landmark feature extraction, LSTM "
        "drowsiness inference, and alert orchestration for the Raspberry Pi 5 in-cabin unit."
    ),
    python_requires=">=3.11",
    package_dir={"cv_argus": "src"},
    packages=[
        "cv_argus",
        "cv_argus.alerts",
        "cv_argus.model",
        "cv_argus.pipeline",
        "cv_argus.orchestrator",
        "cv_argus.buffer",
        "cv_argus.sender",
    ],
    # Keep this list in sync with requirements.txt by hand — the Dockerfile installs from
    # requirements.txt, then runs `pip install -e . --no-deps` for this package, so
    # requirements.txt (not this list) is what actually lands in the image. This list still
    # matters for anyone installing the package outside Docker (`pip install -e .`).
    install_requires=[
        "mediapipe>=0.10.14,<0.11",
        "tensorflow>=2.16,<2.17",
        "opencv-python-headless>=4.9,<5",
        "numpy>=1.26,<2",
        "gdown>=5.2,<6",
        "python-dotenv>=1.0,<2",
        # picamera2 (the Pi's CSI camera) is not listed: it depends on the `libcamera` Python
        # bindings, which only ship via Raspberry Pi OS apt (`python3-picamera2`), not PyPI.
        # The Dockerfile installs it that way; outside Docker on a Pi, `apt install
        # python3-picamera2` and use a venv with `--system-site-packages`.
    ],
    entry_points={
        "console_scripts": [
            "cv-argus-run=cv_argus.main:main",
        ],
    },
)
