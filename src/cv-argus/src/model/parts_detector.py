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

**Two interchangeable backends** (`PARTS_PRECISION`), since int8 may cost some accuracy:
- `int8` (default): the full-integer `.tflite`, through `tf.lite.Interpreter` with **1 thread**.
  That was measured fastest: a model this small doesn't split across cores (1.35 ms on 1 thread
  vs 1.69 ms on 4).
- `float`: the original `.keras`, called through a fixed-shape `tf.function` (the same trick as
  `fused_detector.py`, to avoid eager-dispatch overhead).

On 40 real test face crops the two agreed on 97% of grid-cell classes. Both expose
`predict(x) -> raw grid`, so everything downstream is identical.

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


def _sig(x):
    return 1.0 / (1.0 + np.exp(-x))


def decode_grid(raw: np.ndarray, conf_floor: float = constants.PARTS_CONF_THRESHOLD,
                iou_thr: float = constants.PARTS_NMS_IOU, pre_nms: int = 200) -> PartDetections:
    """One image's raw grid (G, G, 5 + C) → detections after NMS (crop-normalised xyxy)."""
    g = raw.shape[0]
    j, i = np.meshgrid(np.arange(g), np.arange(g))
    cx = (j + 2 * _sig(raw[..., 1]) - 0.5) / g
    cy = (i + 2 * _sig(raw[..., 2]) - 0.5) / g
    w, h = _sig(raw[..., 3]), _sig(raw[..., 4])
    cls_p = _sig(raw[..., 5:])
    cls = cls_p.argmax(-1)
    score = _sig(raw[..., 0]) * cls_p.max(-1)
    m = score > conf_floor
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

    def __init__(self, backend, precision: str, conf_threshold: float = constants.PARTS_CONF_THRESHOLD):
        self._backend = backend
        self.precision = precision
        self._conf = conf_threshold
        self.last_phase_seconds: dict[str, float] = {}

    @classmethod
    def from_env(cls) -> "PartsDetector":
        from .downloader import download_parts_float_model, download_parts_int8_model
        precision = (os.environ.get("PARTS_PRECISION") or constants.PARTS_DEFAULT_PRECISION).strip().lower()
        if precision == "int8":
            backend = _TFLiteBackend(download_parts_int8_model(), constants.PARTS_TFLITE_THREADS)
        elif precision == "float":
            backend = _KerasBackend(download_parts_float_model())
        else:
            raise SystemExit(f"Unknown PARTS_PRECISION={precision!r} -- expected 'int8' or 'float'")
        logger.info("parts detector loaded (%s)", precision)
        return cls(backend, precision)

    def detect(self, x: np.ndarray) -> PartDetections:
        t0 = time.perf_counter()
        raw = self._backend(x)
        t1 = time.perf_counter()
        dets = decode_grid(raw, self._conf)
        self.last_phase_seconds = {"infer": t1 - t0, "decode": time.perf_counter() - t1}
        return dets
