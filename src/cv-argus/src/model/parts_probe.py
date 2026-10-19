"""Checks that a parts-detector model file actually loads and runs, **in a child process**.

Why a child process: when the int8 `.tflite` fails on a device (seen on the Raspberry Pi 5), it
may not raise a Python exception at all. TFLite's native kernels can die with an illegal
instruction or a segfault, which kills the whole interpreter, and no `try/except` can catch
that. So the check loads the model and runs two inferences in a throwaway `python -c` child;
the parent only reads its exit status. A crash, a timeout, a wrong output shape or non-finite
output all count as "not usable".

The child script is a string (`_CHILD`) that imports only numpy and tensorflow, never
`cv_argus`. That way it works both from an installed package (Docker) and a bare checkout
(tests).

Two users:
- `PartsDetector.from_env()` probes the int8 file at startup and falls back to the float
  `.keras` if it isn't usable (`PARTS_INT8_FALLBACK=0` turns the fallback off).
- The Dockerfile runs `python -m cv_argus.model.parts_probe` at build time. It probes both
  files on the build machine's architecture and prints a report, including how closely int8
  and float agree. The build fails only if the float model is unusable, since that's the last
  resort; a broken int8 just means the container will run float.
"""

from __future__ import annotations

import logging
import subprocess
import sys
from pathlib import Path

from .. import constants

logger = logging.getLogger(__name__)

GRID = constants.PARTS_IMG_SIZE // 8                   # 160 / stride 8 = 20
OUT_CHANNELS = 5 + len(constants.PARTS_CLASSES)        # objectness + box + classes = 10
PROBE_TIMEOUT_S = 120.0

# argv: precision, model path, input side, grid, channels, threads, [save .npy path]
_CHILD = r"""
import sys
import numpy as np
precision, path, side, grid, ch, threads = sys.argv[1], sys.argv[2], *map(int, sys.argv[3:7])
save = sys.argv[7] if len(sys.argv) > 7 else None
import tensorflow as tf
shape = (1, side, side, 1)
if precision == "int8":
    it = tf.lite.Interpreter(model_path=path, num_threads=threads)
    it.allocate_tensors()
    i, o = it.get_input_details()[0]["index"], it.get_output_details()[0]["index"]
    def run(x):
        it.set_tensor(i, x); it.invoke(); return it.get_tensor(o)[0]
else:
    model = tf.keras.models.load_model(path, compile=False)
    run = lambda x: model(x, training=False).numpy()[0]
rng = np.random.default_rng(0)
outs = [run(np.zeros(shape, np.float32)), run(rng.random(shape, dtype=np.float32))]
for out in outs:
    if out.shape != (grid, grid, ch):
        sys.exit(f"output shape {out.shape}, expected {(grid, grid, ch)}")
    if not np.isfinite(out).all():
        sys.exit("non-finite output")
if save:
    np.save(save, outs[1])
print(f"ok: output {outs[1].shape}, range [{outs[1].min():.3f}, {outs[1].max():.3f}]")
"""


def probe(precision: str, path: str | Path, timeout: float = PROBE_TIMEOUT_S,
          save_output: str | Path | None = None) -> tuple[bool, str]:
    """Load + run `path` in a child process. Returns (usable, one-line detail)."""
    args = [sys.executable, "-c", _CHILD, precision, str(path), str(constants.PARTS_IMG_SIZE),
            str(GRID), str(OUT_CHANNELS), str(constants.PARTS_TFLITE_THREADS)]
    if save_output is not None:
        args.append(str(save_output))
    try:
        res = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return False, f"timed out after {timeout:.0f} s"
    if res.returncode == 0:
        return True, (res.stdout.strip().splitlines() or ["ok"])[-1]
    if res.returncode < 0:
        detail = f"crashed with signal {-res.returncode}"
    else:
        lines = [ln for ln in res.stderr.strip().splitlines() if ln.strip()]
        detail = lines[-1] if lines else f"exit code {res.returncode}"
    return False, detail


def main() -> int:
    """Build-time report: probe both precisions on this machine. Fails only if float is broken."""
    import tempfile

    import numpy as np

    from .downloader import download_parts_float_model, download_parts_int8_model

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    with tempfile.TemporaryDirectory() as tmp:
        outs = {}
        results = {}
        for precision, download in (("int8", download_parts_int8_model), ("float", download_parts_float_model)):
            outs[precision] = Path(tmp) / f"{precision}.npy"
            results[precision] = probe(precision, download(), save_output=outs[precision])
            ok, detail = results[precision]
            logger.info("parts model check: %-5s %s  (%s)", precision, "OK" if ok else "FAILED", detail)
        if all(ok for ok, _ in results.values()):
            a, b = np.load(outs["int8"]), np.load(outs["float"])
            sig = lambda v: 1.0 / (1.0 + np.exp(-v))
            same_cls = (a[..., 5:].argmax(-1) == b[..., 5:].argmax(-1)).mean()
            logger.info("parts model check: int8 vs float on a random input: max |Δσ| = %.3f, "
                        "same class in %.0f%% of cells", np.abs(sig(a) - sig(b)).max(), 100 * same_cls)
    if not results["float"][0]:
        logger.error("parts model check: the float model is unusable; there is nothing to fall back to")
        return 1
    if not results["int8"][0]:
        logger.warning("parts model check: int8 is unusable on this machine; the container will "
                       "fall back to the float model at startup (PARTS_INT8_FALLBACK)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
