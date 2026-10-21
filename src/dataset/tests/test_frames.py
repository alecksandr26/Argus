"""frames.py — capture plan, the one-still-per-interval loop (fake camera + clock), video
extraction, session naming, inventory."""

import csv
import importlib
import math

import pytest

cv2 = pytest.importorskip("cv2")
np = pytest.importorskip("numpy")


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("ARGUS_DATASET_ROOT", str(tmp_path))
    from argus_dataset import paths
    importlib.reload(paths)
    from argus_dataset import collect, frames
    importlib.reload(collect)
    importlib.reload(frames)
    paths.ensure_dirs()
    return paths, frames


class FakeCamera:
    """``fps`` frames per (fake) second; each read advances the shared clock by one frame."""

    def __init__(self, fps=20, fail_after=None):
        self.fps, self.ticks, self.fail_after = fps, 0, fail_after

    def clock(self):
        return self.ticks / self.fps

    def read(self):
        if self.fail_after is not None and self.ticks >= self.fail_after:
            return False, None
        self.ticks += 1
        frame = np.full((48, 64, 3), self.ticks % 256, np.uint8)
        return True, frame


def _rows(session):
    with open(session.csv_path, newline="") as fh:
        return list(csv.DictReader(fh))


# --- plan ---------------------------------------------------------------------------------

def test_guided_plan_alternates_unsaved_transitions(env):
    _, frames = env
    plan = frames.guided_plan((("a", "do a"), ("b", "do b")), phase_sec=5, transition_sec=2,
                              rounds=2)
    assert [(s.tag, s.save) for s in plan] == [("a", False), ("a", True), ("b", False),
                                               ("b", True)] * 2
    assert frames.plan_duration(plan) == 28
    assert frames.segment_at(plan, 2.5).save and frames.segment_at(plan, 2.5).tag == "a"
    assert frames.segment_at(plan, 28) is None


def test_free_plan_zero_duration_runs_forever(env):
    _, frames = env
    (seg,) = frames.free_plan("x", 0)
    assert math.isinf(seg.end) and seg.save
    assert frames.segment_at([seg], 1e9) is seg


# --- live capture ---------------------------------------------------------------------------

def test_capture_one_still_per_second_per_phase(env):
    _, frames = env
    cam = FakeCamera()
    session = frames.new_session("subject_55")
    plan = frames.guided_plan((("eyes_closed", "close"), ("head_down", "down")),
                              phase_sec=3, transition_sec=1)
    res = frames.capture_stream(cam, session, plan, interval_sec=1.0, preview=False,
                                countdown_sec=0, clock=cam.clock)

    assert dict(session.saved) == {"eyes_closed": 3, "head_down": 3}
    rows = _rows(session)
    assert [r["tag"] for r in rows] == ["eyes_closed"] * 3 + ["head_down"] * 3
    assert [float(r["t_sec"]) for r in rows] == [1, 2, 3, 5, 6, 7]
    for r in rows:
        assert (session.dir / r["filename"]).exists()
        assert r["filename"].startswith(f"subject_55_{session.name}_")
    assert res.n_read == 8 * 20 and not res.stopped_early and res.video_path is None


def test_capture_saved_still_is_the_clean_frame(env):
    _, frames = env
    cam = FakeCamera()
    session = frames.new_session("subject_01")
    frames.capture_stream(cam, session, frames.free_plan("x", 0.5), preview=False,
                          countdown_sec=0, clock=cam.clock)
    (row,) = _rows(session)
    img = cv2.imread(str(session.dir / row["filename"]))
    assert img.shape == (48, 64, 3)
    assert int(img.std()) == 0  # uniform synthetic frame: nothing drawn on it


def test_capture_stops_when_camera_dies_and_keeps_stills(env):
    _, frames = env
    cam = FakeCamera(fail_after=45)
    session = frames.new_session("subject_01")
    res = frames.capture_stream(cam, session, frames.free_plan("x", 10), preview=False,
                                countdown_sec=0, clock=cam.clock)
    assert res.notes == "camera stopped returning frames"
    assert sum(session.saved.values()) == 2  # t = 0 and t = 1 (warm-up ate 5 frames)


def test_capture_save_video(env):
    _, frames = env
    cam = FakeCamera()
    session = frames.new_session("subject_01")
    res = frames.capture_stream(cam, session, frames.free_plan("x", 2), preview=False,
                                countdown_sec=0, save_video=True, clock=cam.clock)
    assert res.video_path == session.dir / "session.mp4" and res.video_path.exists()
    assert not (session.dir / ".session.partial.mp4").exists()
    cap = cv2.VideoCapture(str(res.video_path))
    assert int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) == res.n_read == 40
    cap.release()


# --- video files ----------------------------------------------------------------------------

def test_extract_from_video_uses_video_time(env, tmp_path):
    _, frames = env
    src = tmp_path / "clip.mp4"
    wr = cv2.VideoWriter(str(src), cv2.VideoWriter_fourcc(*"mp4v"), 10.0, (64, 48))
    for i in range(52):  # 5.2 s at 10 fps
        wr.write(np.full((48, 64, 3), i * 4, np.uint8))
    wr.release()

    session = frames.new_session("subject_02")
    frames.extract_from_video(src, session, interval_sec=1.0, tag="phone")
    rows = _rows(session)
    assert [float(r["t_sec"]) for r in rows] == [0, 1, 2, 3, 4, 5]
    assert {r["tag"] for r in rows} == {"phone"}


# --- naming + inventory ---------------------------------------------------------------------

def test_next_subject_id_spans_both_trees(env):
    paths, frames = env
    assert frames.next_subject_id() == "subject_01"
    (paths.raw_dir() / "subject_54").mkdir(parents=True)
    assert frames.next_subject_id() == "subject_55"
    (paths.frames_raw_dir() / "subject_60").mkdir(parents=True)
    assert frames.resolve_subject("new") == "subject_61"
    assert frames.resolve_subject("7") == "subject_07"


def test_new_session_never_reuses_a_folder(env):
    _, frames = env
    a = frames.new_session("subject_01", now=0)
    b = frames.new_session("subject_01", now=0)
    assert a.dir != b.dir and b.name == f"{a.name}_2"


def test_inventory_counts_tags_per_subject(env):
    _, frames = env
    frame = np.zeros((8, 8, 3), np.uint8)
    for subject, tags in {"subject_01": ["a", "a", "b"], "subject_02": ["b"]}.items():
        s = frames.new_session(subject)
        for t in tags:
            frames.save_still(s, frame, tag=t, t_sec=0, source="test")
    inv = frames.inventory()
    assert inv["subject_01"] == {"a": 2, "b": 1} and inv["subject_02"] == {"b": 1}
