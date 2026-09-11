"""Runtime knobs that MUST be set before numpy / tensorflow / opencv / mediapipe load.

`import cv_argus.bootstrap` is the very first line of `main.py` and `__main__.py`. The native
libraries read most of these thread-count env vars exactly once, at load time, so setting them
after `import tensorflow` (etc.) is silently too late.

**Raspberry Pi 5 simulation.** The truck-cabin target is a Pi 5 (4 cores, no dGPU). When
`CV_ARGUS_NUM_THREADS` is set (docker-compose.yml defaults it to 4), this pins every BLAS /
OpenMP / TensorFlow thread pool to that count so a 12-core dev box behaves like the real
hardware. The hard ceiling is the container's `cpuset` / `cpus` limit (see docker-compose.yml)
-- MediaPipe's XNNPACK pool has no Python knob and only the cgroup constrains it; these env
vars just stop the *other* libraries from oversubscribing within that ceiling. Leave
`CV_ARGUS_NUM_THREADS` unset for an unthrottled local run.

Mirrors `src/dataset/argus_dataset/bootstrap.py`, which uses the same import-first pattern.
"""

from __future__ import annotations

import os

# Every knob a CPU-math library checks for its thread-pool size, at load time.
_THREAD_ENV = (
    "OMP_NUM_THREADS",
    "OPENBLAS_NUM_THREADS",
    "MKL_NUM_THREADS",
    "NUMEXPR_NUM_THREADS",
    "VECLIB_MAXIMUM_THREADS",
    "TF_NUM_INTRAOP_THREADS",
    "TF_NUM_INTEROP_THREADS",
)


def configure() -> None:
    n = os.environ.get("CV_ARGUS_NUM_THREADS", "").strip()
    if n:
        for var in _THREAD_ENV:
            os.environ.setdefault(var, n)

    # This module is CPU-only by design (see the fused-detector / MediaPipe docstrings); never
    # let TF or MediaPipe try to grab a GPU, and keep TF's C++ logging quiet.
    os.environ.setdefault("CUDA_VISIBLE_DEVICES", "-1")
    os.environ.setdefault("MEDIAPIPE_DISABLE_GPU", "1")
    os.environ.setdefault("TF_CPP_MIN_LOG_LEVEL", "2")

    if n:
        try:
            import cv2  # noqa: PLC0415 -- deliberately after the env vars above

            cv2.setNumThreads(int(n))
        except Exception:  # cv2 missing / weird build -- the env vars above still apply
            pass


configure()
