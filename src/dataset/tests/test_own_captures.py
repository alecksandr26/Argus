"""own_captures.py — guided stills → a head-pose source, head_down from tag + pitch agreement.
MediaPipe is never run here: ``mp_fn`` is a stub returning hand-written cache rows."""

import csv
import importlib

import numpy as np
import pytest

cv2 = pytest.importorskip("cv2")


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("ARGUS_DATASET_ROOT", str(tmp_path))
    from argus_dataset import paths
    importlib.reload(paths)
    from argus_dataset import detection_dataset, external, frames, own_captures, parts_dataset
    for m in (external, frames, parts_dataset, detection_dataset, own_captures):
        importlib.reload(m)
    paths.ensure_dirs()
    return paths, external, detection_dataset, own_captures, parts_dataset


def _mp(face=True, pose=True, pitch="0", yaw="0"):
    return {"face_found": "1" if face else "0", "landmarks_found": "1" if pose else "0",
            "fx": "20", "fy": "20", "fw": "60", "fh": "60", "pitch": pitch, "yaw": yaw, "roll": "0",
            "a_x0": "0.30", "a_y0": "0.38", "a_x1": "0.42", "a_y1": "0.42", "a_ear": "0.35",
            "b_x0": "0.58", "b_y0": "0.38", "b_x1": "0.70", "b_y1": "0.42", "b_ear": "0.35",
            "m_x0": "0.40", "m_y0": "0.62", "m_x1": "0.60", "m_y1": "0.72", "jaw_open": "0.05"}


@pytest.mark.parametrize("tag,mp,sharp,expected", [
    ("head_down", _mp(pitch="25"), None, ("head_down", "pitch")),
    ("head_down", _mp(pitch="16"), None, ("head_down", "pitch")),     # pitch alone: head_straight
    ("head_down_eyes_closed", _mp(pose=False), None, ("head_down", "no_landmarks")),
    ("head_down", _mp(pitch="12"), None, ("skip", "not_down")),       # asked, but not lowered yet
    ("head_down", _mp(face=False), None, ("skip", "no_face")),
    ("straight", _mp(pitch="25"), None, ("auto", "")),                # never labelled from the tag
    ("eyes_closed", _mp(), 5.0, ("skip", "blurry")),
])
def test_plan_still(env, tag, mp, sharp, expected):
    assert env[3].plan_still(tag, mp, sharp, min_sharpness=10.0) == expected


def test_out_name_groups_by_subject(env):
    oc, pd_ = env[3], env[4]
    a = oc.out_name("subject_55", "20261023-100000", "x_0001_head_down.jpg")
    b = oc.out_name("subject_55", "20261024-090000", "x_0007_straight.jpg")
    assert a != b
    assert pd_.original_stem(a) == pd_.original_stem(b) == "subject_55"


def _session(paths, subject, session, stills):
    d = paths.frames_raw_dir() / subject / session
    d.mkdir(parents=True)
    with open(d / "frames.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["filename", "subject", "session", "tag", "sharpness"])
        w.writeheader()
        for i, tag in enumerate(stills, 1):
            name = f"{subject}_{session}_{i:04d}_{tag}.jpg"
            cv2.imwrite(str(d / name), np.full((100, 100, 3), 128, np.uint8))
            w.writerow({"filename": name, "subject": subject, "session": session, "tag": tag,
                        "sharpness": "50"})
    return d


def test_export_feeds_the_detection_builder(env):
    paths, external, dd, oc, _ = env
    _session(paths, "subject_55", "s1", ["straight", "head_down", "head_down"])
    pitches = iter(["0", "16", "2"])      # straight; lowered (kept); not yet lowered (dropped)
    summary = oc.export(lambda p: _mp(pitch=next(pitches)))
    assert summary["kept"] == 2 and summary["head_down_boxes"] == 1
    assert summary["by_tag"]["head_down: skip (not_down)"] == 1
    assert summary["pitch_by_tag"]["head_down"]["n"] == 2 and summary["pitch_by_tag"]["straight"]["p50"] == 0

    e = external.load_manifest()["sources"][oc.manifest_key()]
    assert e["kind"] == "roboflow" and e["class_names"] == ["head_down"] and e["images"] == 2
    dd.check_head_maps(external.load_manifest())          # every class mapped
    imgs = dd.roboflow_images(external.load_manifest())
    assert len(imgs) == 2

    plans = {}
    for p in imgs:
        lbl = p.parent.parent / "labels" / (p.stem + ".txt")
        plans[bool(lbl.read_text().strip())] = dd.plan_roboflow_image(p, e, _mp(pitch="16"), 100, 100)
    down, auto = plans[True], plans[False]
    assert down["status"] == "ok" and down["head_class"] == "head_down" and down["head_source"] == "human"
    assert down["lm_head_class"] == "head_straight"       # what 16° alone would have said
    assert auto["head_class"] == "head_straight" and auto["head_source"] == "landmarks"   # no tag label


def test_export_rebuilds_and_drops_empty_source(env):
    paths, external, _, oc, _ = env
    d = _session(paths, "subject_56", "s1", ["head_down"])
    assert oc.export(lambda p: _mp(pitch="30"))["kept"] == 1
    for f in d.glob("*.jpg"):
        f.unlink()
    assert oc.export(lambda p: _mp(pitch="30"))["kept"] == 0
    assert oc.manifest_key() not in external.load_manifest()["sources"]
    assert not list((oc.project_dir() / "train" / "images").iterdir())
