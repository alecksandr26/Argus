"""Face-crop geometry for the "parts" detector: the square crop it was trained on, and mapping
its boxes back onto the camera frame.

**Training fidelity.** `square_face_box` and `crop_square` are ported **verbatim** from
`src/dataset/argus_dataset/detection_dataset.py`, which built `parts_det.zip`. The detector
learned on exactly this crop:
- `max(w, h) × (1 + 2 × 0.25)` around the raw BlazeFace box, square, centred;
- black padding wherever the square leaves the frame;
- resized, then converted to grayscale.

`src/dataset/tests/test_parts_crop_equiv.py` diffs the two copies. Don't "improve" one without the
other, or the model silently sees a different framing than it was trained on.

**Box remapping.** The detector returns boxes normalised (0–1) to *its crop*. The crop is the
square `(x0, y0, side)` in full-frame pixels, and may stick out past the frame edge, which is the
padded part. So a crop-normalised point `(u, v)` sits at frame pixel `(x0 + u·side, y0 + v·side)`.
Nothing else is needed: the stored-crop resize (192) and the model resize (160) both cancel out,
because the boxes are normalised. The demo overlay draws on the same `frame_bgr` that inference
used, at its native resolution, so the remapped boxes line up with the stream by construction. A
downscaled stream (`DEMO_STREAM_WIDTH`) resizes the *finished* annotated image, never the boxes.

No `cv2` import at module level, so this stays importable and testable without OpenCV.
"""

from __future__ import annotations

import numpy as np

from .. import constants


def square_face_box(fx, fy, fw, fh, margin=constants.PARTS_FACE_MARGIN_FRAC):
    """BlazeFace pixel box → square crop (x0, y0, side) grown by ``margin`` per side. It may
    extend past the image; ``crop_square`` pads that part with black rather than distorting."""
    side = max(fw, fh) * (1 + 2 * margin)
    cx, cy = fx + fw / 2, fy + fh / 2
    return cx - side / 2, cy - side / 2, side


def crop_square(img: np.ndarray, x0: float, y0: float, side: float, out: int) -> np.ndarray:
    import cv2
    H, W = img.shape[:2]
    x0i, y0i, s = int(round(x0)), int(round(y0)), max(1, int(round(side)))
    pad_l, pad_t = max(0, -x0i), max(0, -y0i)
    pad_r, pad_b = max(0, x0i + s - W), max(0, y0i + s - H)
    sub = img[max(0, y0i):min(H, y0i + s), max(0, x0i):min(W, x0i + s)]
    if pad_l or pad_t or pad_r or pad_b:
        sub = cv2.copyMakeBorder(sub, pad_t, pad_b, pad_l, pad_r, cv2.BORDER_CONSTANT, value=0)
    return cv2.resize(sub, (out, out), interpolation=cv2.INTER_AREA)


def model_input_from_frame(frame_bgr: np.ndarray, face_box_raw, size: int = constants.PARTS_IMG_SIZE):
    """Frame + raw BlazeFace box → (model input (1, size, size, 1) float32 in 0–1, crop square).

    The same two steps as training: crop at the stored size, then gray, then resize to the model's
    input size (both INTER_AREA). Real NoIR frames only go to gray here; IR *simulation* is a
    training-time augmentation, never applied at inference.
    """
    import cv2
    x0, y0, side = square_face_box(*face_box_raw)
    crop = crop_square(frame_bgr, x0, y0, side, constants.PARTS_STORED_CROP_SIZE)
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    if gray.shape[0] != size:
        gray = cv2.resize(gray, (size, size), interpolation=cv2.INTER_AREA)
    return gray.astype(np.float32)[None, :, :, None] / 255.0, (x0, y0, side)


def remap_crop_boxes(boxes_norm: np.ndarray, crop: tuple[float, float, float],
                     frame_wh: tuple[int, int]) -> np.ndarray:
    """(n, 4) crop-normalised xyxy → (n, 4) frame-pixel xyxy, clipped to the frame."""
    if len(boxes_norm) == 0:
        return np.zeros((0, 4), np.float32)
    x0, y0, side = crop
    w, h = frame_wh
    out = np.asarray(boxes_norm, np.float32) * side + np.array([x0, y0, x0, y0], np.float32)
    out[:, [0, 2]] = out[:, [0, 2]].clip(0, w - 1)
    out[:, [1, 3]] = out[:, [1, 3]].clip(0, h - 1)
    return out
