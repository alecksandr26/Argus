"""parts_tracker: stable IDs, survival through misses, majority-vote smoothing."""

import numpy as np

from cv_argus.model.parts_detector import CLOSED_EYE, HEAD_DOWN, OPEN_EYE, YAWN
from cv_argus.model.parts_tracker import PartsTracker

L_EYE = [100, 100, 130, 115]
R_EYE = [160, 100, 190, 115]


def step(tr, dets):
    if not dets:
        return tr.update([], [], [])
    b, c = zip(*dets)
    return tr.update(np.array(b, np.float32), np.full(len(b), 0.9), np.array(c))


def test_ids_are_stable_and_per_kind():
    tr = PartsTracker()
    t = step(tr, [(L_EYE, OPEN_EYE), (R_EYE, OPEN_EYE), ([120, 150, 170, 190], YAWN)])
    ids = sorted(x.id for x in t)
    assert ids == ["E1", "E2", "M1"]
    moved = [[x + 2 for x in L_EYE], [x + 2 for x in R_EYE]]
    t2 = step(tr, [(moved[0], OPEN_EYE), (moved[1], OPEN_EYE)])
    assert sorted(x.id for x in t2 if x.kind == "eye") == ["E1", "E2"]


def test_track_survives_short_misses_then_dies():
    tr = PartsTracker(max_misses=3)
    step(tr, [(L_EYE, CLOSED_EYE)])
    for _ in range(3):
        t = step(tr, [])
        assert len(t) == 1 and not t[0].visible and t[0].label == "closed_eye"
    assert step(tr, []) == []


def test_majority_vote_smooths_a_single_flicker():
    tr = PartsTracker()
    for c in (CLOSED_EYE, CLOSED_EYE, OPEN_EYE):
        t = step(tr, [(L_EYE, c)])
    assert t[0].label == "closed_eye"          # 2 of the last 3 frames were closed
    t = step(tr, [(L_EYE, OPEN_EYE)])
    assert t[0].label == "open_eye"


def test_eye_and_head_never_share_a_track():
    tr = PartsTracker()
    t = step(tr, [([90, 60, 200, 220], HEAD_DOWN), ([95, 65, 205, 225], OPEN_EYE)])
    assert sorted(x.kind for x in t) == ["eye", "head"]


HEAD = [80, 60, 220, 240]


def test_head_is_down_once_one_recent_frame_says_so():
    tr = PartsTracker(head_down_votes=1)
    from cv_argus.model.parts_detector import HEAD_STRAIGHT
    for c in (HEAD_STRAIGHT, HEAD_DOWN, HEAD_STRAIGHT):
        t = step(tr, [(HEAD, c)])
    assert t[0].label == "head_down"
    for _ in range(2):
        t = step(tr, [(HEAD, HEAD_STRAIGHT)])
    assert t[0].label == "head_straight"        # the down frame has left the 3-frame window


def test_head_down_votes_two_needs_a_majority():
    tr = PartsTracker(head_down_votes=2)
    from cv_argus.model.parts_detector import HEAD_STRAIGHT
    for c in (HEAD_STRAIGHT, HEAD_DOWN, HEAD_STRAIGHT):
        t = step(tr, [(HEAD, c)])
    assert t[0].label == "head_straight"


def test_reset_forgets_tracks_and_restarts_ids():
    """A face switch resets the tracker: the old face's closed eyes don't carry over."""
    tr = PartsTracker()
    for _ in range(3):
        step(tr, [(L_EYE, CLOSED_EYE), (R_EYE, CLOSED_EYE)])
    tr.reset()
    assert tr.tracks == []
    t = step(tr, [(L_EYE, OPEN_EYE)])
    assert [(x.id, x.label) for x in t] == [("E1", "open_eye")]
