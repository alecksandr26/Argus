"""Thread pinning — import this FIRST, before pandas / numpy / cv2 / mediapipe.

The pipeline parallelises across clips (one worker == one clip), so every clip's video decode
+ MediaPipe inference + NumPy math must run single-threaded; otherwise N workers each spawning
a full BLAS/OpenMP thread pool oversubscribes the CPU badly. Setting these as env vars before
the native libraries load is the only reliable way to pin them.
"""

from __future__ import annotations

import os

_THREAD_ENV = (
    "OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS",
    "NUMEXPR_NUM_THREADS", "VECLIB_MAXIMUM_THREADS",
    "TF_NUM_INTRAOP_THREADS", "TF_NUM_INTEROP_THREADS",
)


def configure_process_threads() -> None:
    for var in _THREAD_ENV:
        os.environ.setdefault(var, "1")
    os.environ.setdefault("CUDA_VISIBLE_DEVICES", "-1")   # never touch the GPU from a fork/spawn
    os.environ.setdefault("GLOG_minloglevel", "2")
    os.environ.setdefault("TF_CPP_MIN_LOG_LEVEL", "3")
    os.environ.setdefault("MEDIAPIPE_DISABLE_GPU", "1")


configure_process_threads()
