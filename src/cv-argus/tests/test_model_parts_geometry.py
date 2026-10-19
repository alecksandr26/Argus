"""parts_geometry: the training crop, and crop → frame box remapping (the demo-overlay contract)."""

import numpy as np
import pytest

from cv_argus.model import parts_geometry as pg

cv2 = pytest.importorskip("cv2")


def test_square_face_box_is_1_5x_and_centred():
    x0, y0, side = pg.square_face_box(100, 50, 40, 60)
    assert side == pytest.approx(90.0)                     # max(40, 60) * 1.5
    assert (x0 + side / 2, y0 + side / 2) == pytest.approx((120.0, 80.0))


def test_crop_square_pads_outside_the_frame_with_black():
    img = np.full((100, 100, 3), 200, np.uint8)
    out = pg.crop_square(img, -20, -20, 60, 60)
    assert out.shape == (60, 60, 3)
    assert out[0, 0].sum() == 0 and out[-1, -1].sum() == 600


def test_model_input_shape_range_and_gray():
    frame = np.random.default_rng(0).integers(0, 255, (480, 640, 3), dtype=np.uint8)
    x, sq = pg.model_input_from_frame(frame, (300, 200, 80, 100))
    assert x.shape == (1, 160, 160, 1) and x.dtype == np.float32
    assert 0.0 <= x.min() and x.max() <= 1.0
    assert sq[2] == pytest.approx(150.0)


@pytest.mark.parametrize("face,frame_wh", [
    ((300, 200, 80, 100), (640, 480)),       # face well inside
    ((-15, 10, 90, 120), (640, 480)),        # face cut off at the left edge (padded crop)
    ((560, 380, 90, 120), (641, 479)),       # bottom-right corner, odd-sized frame
])
def test_remap_round_trip(face, frame_wh):
    """A box in frame pixels -> crop-normalised (what the model outputs) -> back to frame
    pixels must land on the same pixels, also when the crop sticks out of the frame."""
    x0, y0, side = pg.square_face_box(*face)
    w, h = frame_wh
    eye_px = np.array([[x0 + 0.3 * side, y0 + 0.35 * side, x0 + 0.42 * side, y0 + 0.42 * side]], np.float32)
    eye_px[:, [0, 2]] = eye_px[:, [0, 2]].clip(0, w - 1)
    eye_px[:, [1, 3]] = eye_px[:, [1, 3]].clip(0, h - 1)
    norm = (eye_px - np.array([x0, y0, x0, y0])) / side
    back = pg.remap_crop_boxes(norm, (x0, y0, side), frame_wh)
    np.testing.assert_allclose(back, eye_px, atol=1e-3)


def test_remap_clips_to_the_frame_and_handles_empty():
    out = pg.remap_crop_boxes(np.array([[-0.2, -0.2, 1.2, 1.2]]), (10, 10, 100), (50, 40))
    assert out.min() >= 0 and out[0, 2] == 49 and out[0, 3] == 39
    assert pg.remap_crop_boxes(np.zeros((0, 4)), (0, 0, 10), (10, 10)).shape == (0, 4)
