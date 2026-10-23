"""The "parts" detector: notebook 12's from-scratch tiny YOLO (`tiny_yolo_w1`, 46k parameters).

It looks at one square grayscale face crop (160×160×1, 0–1) and finds `open_eye`, `closed_eye`,
`yawn`, `head_straight` and `head_down` boxes. The output is a 20×20 grid with `1 + 4 + 5`
numbers per cell: `[objectness, tx, ty, tw, th, 5 class logits]`, decoded YOLOv5-style
(anchor-free):

    cx = (j + 2σ(tx) − 0.5) / G      cy = (i + 2σ(ty) − 0.5) / G      w = σ(tw)   h = σ(th)
    score = σ(objectness) · max_c σ(class_c)

`decode_grid` and `nms_per_class` are ported verbatim from notebook 12's shared evaluation and
head cells (`grid_postprocess` / `nms_per_class`). Changing them here without changing the
notebook makes the deployed model behave differently from how it was scored.

**Eye size/position filter** (in both copies): before NMS, an eye cell is dropped when its box's
larger side is over `PARTS_EYE_MAX_FRAC` (0.30) of the crop or its centre y is outside
`PARTS_EYE_CY_RANGE` (0.15–0.70). The model learned head-sized "eyes" from full-image eye
close-ups; on a face crop a real eye is never that big, nor at the chin or the top edge.

**Pair decision thresholds** are the one deliberate addition. The notebook takes the plain argmax
class. Here, a cell whose argmax is an eye class is called `closed_eye` when
p_closed / (p_open + p_closed) ≥ `PARTS_CLOSED_EYE_THRESHOLD`, and a head cell is called
`head_down` when p_down / (p_straight + p_down) ≥ `PARTS_HEAD_DOWN_THRESHOLD`. At 0.5 both equal
the argmax (the `decode_grid` defaults). Lower values are more sensitive. Only the class within a
pair changes; the score and the box don't, so `PARTS_CONF_THRESHOLD` still decides whether a part
is detected at all.

**Two interchangeable backends** (`PARTS_PRECISION`), since int8 may cost some accuracy:
- `int8` (default): the full-integer `.tflite`, through `tf.lite.Interpreter` with **1 thread**.
  That was measured fastest: a model this small doesn't split across cores (1.35 ms on 1 thread
  vs 1.69 ms on 4).
- `float`: the original `.keras`, called through a fixed-shape `tf.function` (the same trick as
  the old fused detector, to avoid eager-dispatch overhead).

On 40 real test face crops the two agreed on 97% of grid-cell classes. Both expose
`predict(x) -> raw grid`, so everything downstream is identical.

**int8 → float fallback** (`PARTS_INT8_FALLBACK`, on by default). The int8 model has failed on
a Raspberry Pi 5 while float works, so int8 is never trusted blindly:
- **At startup**, `from_env()` probes the int8 file in a child process (`parts_probe.py`), so a
  native crash can't take down the pipeline. If the probe fails, the detector loads the float
  `.keras` instead and logs why.
- **At runtime**, if the int8 backend raises mid-run, `detect()` switches to float once and
  retries the frame.

Either way, `precision` then reads `float` (so the overlay, logs and metrics show what is really
running), and `fallback_reason` says why. `PARTS_INT8_FALLBACK=0` restores the old fail-hard
behaviour.

No `mediapipe` import here (the `model/` boundary), and `tensorflow` is imported lazily, so the
pure decode/NMS maths is testable without it.
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from .. import constants

logger = logging.getLogger(__name__)

CLASSES = constants.PARTS_CLASSES
OPEN_EYE, CLOSED_EYE, YAWN, HEAD_STRAIGHT, HEAD_DOWN = range(len(CLASSES))


@dataclass
class PartDetections:
    """One frame's detections, in crop-normalised xyxy (0–1)."""
    boxes: np.ndarray = field(default_factory=lambda: np.zeros((0, 4), np.float32))
    scores: np.ndarray = field(default_factory=lambda: np.zeros(0, np.float32))
    classes: np.ndarray = field(default_factory=lambda: np.zeros(0, np.int64))

    def __len__(self) -> int:
        return len(self.scores)


# --- decode + NMS: verbatim from notebook 12 ----------------------------------------------

def iou_xyxy(a, b):
    if len(a) == 0 or len(b) == 0:
        return np.zeros((len(a), len(b)), np.float32)
    x0 = np.maximum(a[:, None, 0], b[None, :, 0]); y0 = np.maximum(a[:, None, 1], b[None, :, 1])
    x1 = np.minimum(a[:, None, 2], b[None, :, 2]); y1 = np.minimum(a[:, None, 3], b[None, :, 3])
    inter = np.clip(x1 - x0, 0, None) * np.clip(y1 - y0, 0, None)
    area = lambda r: (r[:, 2] - r[:, 0]) * (r[:, 3] - r[:, 1])
    return inter / (area(a)[:, None] + area(b)[None, :] - inter + 1e-9)


def nms_per_class(boxes, scores, classes, iou_thr=0.5, max_det=30):
    keep = []
    for c in np.unique(classes):
        idx = np.where(classes == c)[0]
        idx = idx[np.argsort(-scores[idx])]
        while len(idx):
            i = idx[0]
            keep.append(i)
            if len(idx) == 1:
                break
            idx = idx[1:][iou_xyxy(boxes[i:i + 1], boxes[idx[1:]])[0] < iou_thr]
    keep = np.array(sorted(keep, key=lambda i: -scores[i])[:max_det], np.int64)
    return boxes[keep], scores[keep], classes[keep]


def env_float(name: str) -> float:
    raw = os.environ.get(name)
    try:
        return float(raw) if raw not in (None, "") else float(getattr(constants, name))
    except ValueError:
        return float(getattr(constants, name))


def _sig(x):
    return 1.0 / (1.0 + np.exp(-x))


def apply_pair_thresholds(cls: np.ndarray, cls_p: np.ndarray, closed_eye_thr: float,
                          head_down_thr: float) -> np.ndarray:
    """Re-decide open/closed and straight/down by a threshold on the pair's probability ratio
    (0.5 = argmax). Cells whose argmax is outside a pair are left alone."""
    for a, b, thr in ((OPEN_EYE, CLOSED_EYE, closed_eye_thr), (HEAD_STRAIGHT, HEAD_DOWN, head_down_thr)):
        ratio = cls_p[..., b] / (cls_p[..., a] + cls_p[..., b] + 1e-9)
        cls = np.where((cls == a) | (cls == b), np.where(ratio >= thr, b, a), cls)
    return cls


def implausible_eye_mask(cls: np.ndarray, cy: np.ndarray, w: np.ndarray, h: np.ndarray,
                         max_frac: float | None = constants.PARTS_EYE_MAX_FRAC,
                         cy_range: tuple[float, float] | None = constants.PARTS_EYE_CY_RANGE) -> np.ndarray:
    """True where a cell's class is an eye class and its box can't be a real eye on a face crop:
    the larger side is over ``max_frac`` of the crop, or the centre y is outside ``cy_range``
    (both crop-normalised). ``None`` disables that check. Mirrored in notebook 12's
    ``grid_postprocess`` (``eye_filter``)."""
    eye = (cls == OPEN_EYE) | (cls == CLOSED_EYE)
    bad = np.zeros(np.shape(cls), bool)
    if max_frac is not None:
        bad |= np.maximum(w, h) > max_frac
    if cy_range is not None:
        bad |= (cy < cy_range[0]) | (cy > cy_range[1])
    return eye & bad


def decode_grid(raw: np.ndarray, conf_floor: float = constants.PARTS_CONF_THRESHOLD,
                iou_thr: float = constants.PARTS_NMS_IOU, pre_nms: int = 200,
                closed_eye_thr: float = 0.5, head_down_thr: float = 0.5,
                eye_max_frac: float | None = constants.PARTS_EYE_MAX_FRAC,
                eye_cy_range: tuple[float, float] | None = constants.PARTS_EYE_CY_RANGE) -> PartDetections:
    """One image's raw grid (G, G, 5 + C) → detections after NMS (crop-normalised xyxy).

    Eye cells failing the size/position filter (`implausible_eye_mask`) are dropped **before**
    NMS, so a head-sized "eye" can't suppress a real eye box of the same class."""
    g = raw.shape[0]
    j, i = np.meshgrid(np.arange(g), np.arange(g))
    cx = (j + 2 * _sig(raw[..., 1]) - 0.5) / g
    cy = (i + 2 * _sig(raw[..., 2]) - 0.5) / g
    w, h = _sig(raw[..., 3]), _sig(raw[..., 4])
    cls_p = _sig(raw[..., 5:])
    cls = apply_pair_thresholds(cls_p.argmax(-1), cls_p, closed_eye_thr, head_down_thr)
    score = _sig(raw[..., 0]) * cls_p.max(-1)
    m = (score > conf_floor) & ~implausible_eye_mask(cls, cy, w, h, eye_max_frac, eye_cy_range)
    order = np.argsort(-score[m])[:pre_nms]
    boxes = np.stack([cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2], -1)[m][order].clip(0, 1)
    b, s, c = nms_per_class(boxes, score[m][order], cls[m][order], iou_thr)
    return PartDetections(b.astype(np.float32), s.astype(np.float32), c.astype(np.int64))


# --- backends -------------------------------------------------------------------------------

class _TFLiteBackend:
    def __init__(self, path: Path, threads: int):
        import tensorflow as tf
        self._it = tf.lite.Interpreter(model_path=str(path), num_threads=threads)
        self._it.allocate_tensors()
        self._in = self._it.get_input_details()[0]
        self._out = self._it.get_output_details()[0]

    def __call__(self, x: np.ndarray) -> np.ndarray:
        self._it.set_tensor(self._in["index"], x.astype(np.float32))
        self._it.invoke()
        return self._it.get_tensor(self._out["index"])[0]


class _KerasBackend:
    def __init__(self, path: Path):
        import tensorflow as tf
        model = tf.keras.models.load_model(str(path), compile=False)
        shape = (1, constants.PARTS_IMG_SIZE, constants.PARTS_IMG_SIZE, 1)
        self._fn = tf.function(lambda x: model(x, training=False),
                               input_signature=[tf.TensorSpec(shape, tf.float32)])
        self._fn(tf.zeros(shape))   # trace once at startup, not on the first live frame

    def __call__(self, x: np.ndarray) -> np.ndarray:
        return self._fn(x.astype(np.float32)).numpy()[0]


class PartsDetector:
    """`detect(x) -> PartDetections` on a model-ready crop. `backend` is any callable mapping
    (1, 160, 160, 1) float32 → (20, 20, 10); tests inject a stub."""

    def __init__(self, backend, precision: str, conf_threshold: float = constants.PARTS_CONF_THRESHOLD,
                 closed_eye_threshold: float = constants.PARTS_CLOSED_EYE_THRESHOLD,
                 head_down_threshold: float = constants.PARTS_HEAD_DOWN_THRESHOLD,
                 fallback=None, fallback_reason: str | None = None,
                 eye_max_frac: float | None = constants.PARTS_EYE_MAX_FRAC,
                 eye_cy_range: tuple[float, float] | None = constants.PARTS_EYE_CY_RANGE):
        self._backend = backend
        # A zero-arg callable building the float backend, used once if `backend` raises.
        self._fallback = fallback
        self.precision = precision
        self.fallback_reason = fallback_reason
        self._conf = conf_threshold
        self.closed_eye_threshold = closed_eye_threshold
        self.head_down_threshold = head_down_threshold
        self.eye_max_frac = eye_max_frac
        self.eye_cy_range = eye_cy_range
        self.last_phase_seconds: dict[str, float] = {}

    @classmethod
    def from_env(cls) -> "PartsDetector":
        from .downloader import download_parts_float_model, download_parts_int8_model
        from .parts_probe import probe
        precision = (os.environ.get("PARTS_PRECISION") or constants.PARTS_DEFAULT_PRECISION).strip().lower()
        if precision not in ("int8", "float"):
            raise SystemExit(f"Unknown PARTS_PRECISION={precision!r} -- expected 'int8' or 'float'")
        allow_fallback = os.environ.get("PARTS_INT8_FALLBACK", "1").strip() not in ("0", "false", "no")
        float_backend = lambda: _KerasBackend(download_parts_float_model())  # noqa: E731
        backend, fallback, reason = None, None, None
        if precision == "int8":
            path = download_parts_int8_model()
            if allow_fallback:
                ok, detail = probe("int8", path)
                if ok:
                    backend, fallback = _TFLiteBackend(path, constants.PARTS_TFLITE_THREADS), float_backend
                else:
                    reason = f"int8 probe failed: {detail}"
                    logger.warning("parts detector: %s -- falling back to the float .keras model", reason)
                    precision = "float"
            else:
                backend = _TFLiteBackend(path, constants.PARTS_TFLITE_THREADS)
        if backend is None:
            backend = float_backend()
        eye_thr, head_thr = env_float("PARTS_CLOSED_EYE_THRESHOLD"), env_float("PARTS_HEAD_DOWN_THRESHOLD")
        logger.info("parts detector loaded (%s, closed_eye >= %.2f, head_down >= %.2f)",
                    precision, eye_thr, head_thr)
        return cls(backend, precision, closed_eye_threshold=eye_thr, head_down_threshold=head_thr,
                   fallback=fallback, fallback_reason=reason)

    def _switch_to_fallback(self, exc: Exception) -> None:
        self.fallback_reason = f"{self.precision} failed at runtime: {type(exc).__name__}: {exc}"
        logger.error("parts detector: %s -- switching to the float .keras model", self.fallback_reason)
        self._backend, self._fallback, self.precision = self._fallback(), None, "float"

    def detect(self, x: np.ndarray) -> PartDetections:
        t0 = time.perf_counter()
        try:
            raw = self._backend(x)
        except Exception as exc:
            if self._fallback is None:
                raise
            self._switch_to_fallback(exc)
            t0 = time.perf_counter()
            raw = self._backend(x)
        t1 = time.perf_counter()
        dets = decode_grid(raw, self._conf, closed_eye_thr=self.closed_eye_threshold,
                           head_down_thr=self.head_down_threshold, eye_max_frac=self.eye_max_frac,
                           eye_cy_range=self.eye_cy_range)
        self.last_phase_seconds = {"infer": t1 - t0, "decode": time.perf_counter() - t1}
        return dets
