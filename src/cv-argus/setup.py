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
        "cv_argus.model",
        "cv_argus.pipeline",
        # "cv_argus.orchestrator",
        # "cv_argus.buffer",
        # "cv_argus.sender",
        # "cv_argus.alerts",
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
        # Official Raspberry Pi camera stack (libcamera bindings) — only installable/needed
        # on the Pi itself; a USB webcam on a dev machine just uses cv2.VideoCapture and
        # doesn't need this.
        "picamera2>=0.3.21; platform_machine in 'armv7l aarch64'",
    ],
    entry_points={
        "console_scripts": [
            "cv-argus-run=cv_argus.main:main",
        ],
    },
)
