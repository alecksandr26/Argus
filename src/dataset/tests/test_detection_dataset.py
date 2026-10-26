"""detection_dataset.py — face-crop YOLO dataset: geometry, labelling rules, grouping, zip.
MediaPipe is never run here: the per-image MediaPipe cache rows are written by hand."""

import importlib
import io
import zipfile

import numpy as np
import pytest

cv2 = pytest.importorskip("cv2")


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("ARGUS_DATASET_ROOT", str(tmp_path))
    from argus_dataset import paths
    importlib.reload(paths)
    from argus_dataset import detection_dataset, external, parts_dataset
    for m in (external, parts_dataset, detection_dataset):
        importlib.reload(m)
    paths.ensure_dirs()
    return paths, external, detection_dataset


# --- geometry ------------------------------------------------------------------------------

def test_crop_square_pads_outside_frame(env):
    dd = env[2]
    img = np.full((100, 100, 3), 200, np.uint8)
    x0, y0, side = dd.square_face_box(-10, -10, 40, 40, margin=0.0)
    out = dd.crop_square(img, x0, y0, side, 40)
    assert out.shape == (40, 40, 3)
    assert out[0, 0].sum() == 0 and out[-1, -1].sum() == 600   # padded corner black, inside kept


def test_map_box_to_crop(env):
    dd = env[2]
    # crop = x 20..70, y 20..70 of a 100x100 image; box centred at (45,45) size 10x10
    assert dd.map_box_to_crop((0.45, 0.45, 0.1, 0.1), 100, 100, 20, 20, 50) == pytest.approx((0.5, 0.5, 0.2, 0.2))
    assert dd.map_box_to_crop((0.9, 0.9, 0.1, 0.1), 100, 100, 20, 20, 50) is None      # centre outside
    assert dd.map_box_to_crop((0.68, 0.45, 0.2, 0.1), 100, 100, 20, 20, 50) is not None  # 60% visible
    assert dd.map_box_to_crop((0.68, 0.68, 0.2, 0.2), 100, 100, 20, 20, 50) is None      # 36% visible


@pytest.mark.parametrize("pitch,yaw,expected", [
    (0, 0, "head_straight"), (9, 20, "head_straight"), (15, 0, "head_straight"), (25, 0, "head_down"),
    (19, 0, None), (0, 24, None), (0, 40, None), (-25, 0, None),
])
def test_head_class(env, pitch, yaw, expected):
    assert env[2].head_class(pitch, yaw) == expected


def _mp(face=True, pose=True, ear_a=0.35, ear_b=0.35, **kw):
    row = {"face_found": "1" if face else "0", "landmarks_found": "1" if pose else "0",
           "fx": "20", "fy": "20", "fw": "60", "fh": "60", "pitch": "0", "yaw": "0", "roll": "0",
           "a_x0": "0.30", "a_y0": "0.38", "a_x1": "0.42", "a_y1": "0.42", "a_ear": str(ear_a),
           "b_x0": "0.58", "b_y0": "0.38", "b_x1": "0.70", "b_y1": "0.42", "b_ear": str(ear_b),
           "m_x0": "0.40", "m_y0": "0.62", "m_x1": "0.60", "m_y1": "0.72", "jaw_open": "0.05"}
    row.update(kw)
    return row


def test_to_std_crop(env):
    dd = env[2]
    std, wide = dd.square_face_box(20, 20, 60, 60), dd.square_face_box(20, 20, 60, 60, margin=0.75)
    pts = np.array([[0.5, 0.5], [0.0, 0.0]])
    out = dd.to_std_crop(pts, wide, std)
    assert out[0] == pytest.approx([0.5, 0.5])                          # face centre stays put
    assert out[1] == pytest.approx([(wide[0] - std[0]) / std[2]] * 2)   # wide corner lands outside


def test_pseudo_yawn_box(env, monkeypatch):
    dd = env[2]
    monkeypatch.setattr(dd.config, "DET_PSEUDO_YAWN_JAW_CLOSED", 0.2)
    monkeypatch.setattr(dd.config, "DET_PSEUDO_YAWN_JAW_OPEN", 0.5)
    assert dd.pseudo_yawn_box(_mp(jaw_open="0.1"), [], strict=True) == (None, False)   # closed mouth
    assert dd.pseudo_yawn_box(_mp(jaw_open="0.3"), [], strict=True) == (None, True)    # unclear: reject
    assert dd.pseudo_yawn_box(_mp(jaw_open="0.3"), [], strict=False) == (None, False)  # source labels yawns
    for strict in (True, False):
        box, amb = dd.pseudo_yawn_box(_mp(jaw_open="0.8"), [], strict=strict)
        assert not amb and box[:2] == pytest.approx((0.5, 0.67))
    assert dd.pseudo_yawn_box(_mp(jaw_open="0.8"), [("yawn", (0.5, 0.7, 0.2, 0.2))], strict=True) == (None, False)
    assert dd.pseudo_yawn_box(_mp(jaw_open=""), [], strict=True) == (None, False)     # old cache row


def test_pseudo_eye_boxes(env):
    dd = env[2]
    new, amb = dd.pseudo_eye_boxes(_mp(ear_a=0.05, ear_b=0.4), [])
    assert [c for c, _ in new] == ["closed_eye", "open_eye"] and not amb
    covered = [("open_eye", (0.36, 0.40, 0.3, 0.2))]               # big human box over eye "a"
    new, _ = dd.pseudo_eye_boxes(_mp(), covered)
    assert len(new) == 1 and new[0][1][0] == pytest.approx(0.64)
    assert dd.pseudo_eye_boxes(_mp(ear_a=0.2), [])[1] is True        # between thresholds


def test_thumb_similarity_is_flip_invariant(env):
    dd = env[2]
    g = np.random.default_rng(0).integers(0, 255, (40, 40), dtype=np.uint8)
    assert dd.thumb_similarity(dd.thumb(g), dd.thumb(g[:, ::-1].copy())) == pytest.approx(1.0, abs=1e-4)


# --- plan rules on a fake export -----------------------------------------------------------

def _export(paths, external, project, images):
    """images: [(split, name, label_text)]; class ids follow the names list below."""
    names = ["close_eyes", "face", "open_eyes", "yawn"]
    root = paths.roboflow_raw_dir() / project / "v1"
    for split, name, label in images:
        (root / split / "images").mkdir(parents=True, exist_ok=True)
        (root / split / "labels").mkdir(parents=True, exist_ok=True)
        rng = np.random.default_rng(abs(hash(name)) % 2**32)
        cv2.imwrite(str(root / split / "images" / f"{name}.jpg"), rng.integers(0, 255, (100, 100, 3), dtype=np.uint8))
        (root / split / "labels" / f"{name}.txt").write_text(label)
    entry = {"kind": "roboflow", "project": project, "version": 1, "location": f"raw/roboflow/{project}/v1",
             "class_names": names, "licence": "CC BY 4.0", "workspace": "ws"}
    external.upsert_source(f"roboflow/{project}", entry)
    return root, entry


TWO_EYES = "2 0.38 0.40 0.06 0.04\n0 0.62 0.40 0.06 0.04\n"   # open (img-left), closed (img-right)


def test_plan_rules(env, monkeypatch):
    paths, external, dd = env
    root, e = _export(paths, external, "drowsiness-hgjay", [
        ("train", "ok_jpg.rf.1", TWO_EYES), ("train", "rej_jpg.rf.2", "3 0.5 0.7 0.4 0.4\n" + TWO_EYES),
        ("train", "close_jpg.rf.3", "2 0.5 0.5 1.0 1.0\n"), ("train", "noface_jpg.rf.4", "2 0.5 0.5 0.1 0.1\n"),
    ])
    img = lambda n: root / "train" / "images" / f"{n}.jpg"
    p = dd.plan_roboflow_image(img("ok_jpg.rf.1"), e, _mp(), 100, 100)
    assert p["status"] == "ok" and p["kind"] == "face" and p["head_class"] == "head_straight"
    assert sorted(c for c, _ in p["boxes"]) == ["closed_eye", "head_straight", "open_eye"]
    assert p["pseudo_eyes"] == 0                                    # both eyes already labelled
    assert dd.plan_roboflow_image(img("rej_jpg.rf.2"), e, _mp(), 100, 100)["reason"] == "ambiguous_label:yawn"
    c = dd.plan_roboflow_image(img("close_jpg.rf.3"), e, _mp(face=False), 100, 100)
    assert c["status"] == "ok" and c["kind"] == "closeup" and c["head_class"] == ""
    assert dd.plan_roboflow_image(img("noface_jpg.rf.4"), e, _mp(face=False), 100, 100)["reason"] == "no_face"
    one = dd.plan_roboflow_image(img("ok_jpg.rf.1"), e, _mp(pose=False), 100, 100)
    assert one["status"] == "ok"                                   # no pose: no head box, eyes still ok
    monkeypatch.setattr(dd.config, "DET_PSEUDO_EYES", False)
    root2, e2 = _export(paths, external, "drowsiness-axuoa", [("train", "x_jpg.rf.9", "2 0.38 0.40 0.06 0.04\n")])
    e2["class_names"] = ["closed_eyes", "no_yawn", "obstacle", "open_eyes"]
    (root2 / "train" / "labels" / "x_jpg.rf.9.txt").write_text("3 0.38 0.40 0.06 0.04\n")
    assert dd.plan_roboflow_image(root2 / "train/images/x_jpg.rf.9.jpg", e2, _mp(), 100, 100)["reason"] == "eyes_incomplete"


# --- end to end ----------------------------------------------------------------------------

def test_build_and_zip(env):
    paths, external, dd = env
    root, e = _export(paths, external, "drowsiness-hgjay",
                      [("train", f"s{i}_jpg.rf.{k}", TWO_EYES) for i in range(6) for k in "ab"])
    mrl = paths.mrl_eye_raw_dir() / "mrlEyes_2018_01"
    for s in range(1, 4):
        (mrl / f"s{s:04d}").mkdir(parents=True)
        for st in (0, 1):
            cv2.imwrite(str(mrl / f"s{s:04d}" / f"s{s:04d}_0000{st}_0_0_{st}_0_1_01.png"),
                        np.full((24, 24), 60 + 50 * st, np.uint8))
    external.upsert_source("mrl_eye", {"kind": "mrl_eye", "location": "raw/mrl_eye/mrlEyes_2018_01",
                                       "licence": "unverified", "classes": {"closed": 3, "open": 3}})
    cache = {str(p): {**_mp(), "src_image": str(p)} for p in sorted(root.glob("*/images/*"))}
    stale = paths.processed_dir() / "parts_det/gray/test/images/gone.jpg"   # from an "earlier build"
    stale.parent.mkdir(parents=True)
    stale.write_bytes(b"x")
    summary = dd.build(cache)
    assert not stale.exists()
    assert summary["images_ok"] == 12 + 6 and summary["groups_crossing_splits"] == 0

    import pandas as pd
    idx = pd.read_csv(paths.processed_dir() / "parts_det/index.csv", dtype=str, keep_default_na=False)
    rf = idx[idx.source == "roboflow"]
    assert rf.group_id.nunique() == 6                              # a/b augmented copies grouped
    assert (rf.groupby("group_id").split.nunique() == 1).all()

    z = zipfile.ZipFile(dd.export_zip())
    names = set(z.namelist())
    imgs = [n for n in names if "/images/" in n]
    assert len(imgs) == 2 * 18
    assert all(n.replace("/images/", "/labels/")[:-4] + ".txt" in names for n in imgs)
    for line in z.read(next(n for n in names if n.startswith("gray/") and "/labels/" in n and "hgjay" in n)).decode().splitlines():
        c, *box = line.split()
        assert 0 <= int(c) < 5 and all(0 <= float(v) <= 1 for v in box)
    assert {"README.txt", "index.csv", "data_gray.yaml", "data_ir.yaml"} <= names
    assert "names: ['open_eye', 'closed_eye', 'yawn', 'head_straight', 'head_down']" in z.read("data_ir.yaml").decode()
    assert len(pd.read_csv(io.BytesIO(z.read("index.csv")))) == 18


# --- head-pose-only source (human head labels) ---------------------------------------------

HP = "headpose-w4zuc"


def _headpose(env, monkeypatch, images):
    paths, external, dd = env
    root, e = _export(paths, external, HP, images)
    e["class_names"] = ["Down", "Forward", "Left"]
    external.upsert_source(f"roboflow/{HP}", e)
    monkeypatch.setitem(dd.config.ROBOFLOW_HEAD_CLASS_MAP, HP,
                        {"Down": "head_down", "Forward": "head_straight", "Left": None})
    return root, e


def test_human_head_label_beats_pitch(env, monkeypatch):
    dd = env[2]
    root, e = _headpose(env, monkeypatch, [("train", "d_jpg.rf.1", "0 0.5 0.5 0.7 0.7\n")])
    p = dd.plan_roboflow_image(root / "train/images/d_jpg.rf.1.jpg", e, _mp(pitch="0"), 100, 100)
    assert p["status"] == "ok" and p["head_class"] == "head_down" and p["head_source"] == "human"
    assert p["lm_head_class"] == "head_straight"                       # what pitch alone said
    assert p["pseudo_eyes"] == 2                                       # eyes come from landmarks
    heads = [b for c, b in p["boxes"] if c == "head_down"]
    assert len(heads) == 1 and heads[0][2] == pytest.approx(60 / 90)   # face-box scope, not head box


def test_headpose_fallback_without_face(env, monkeypatch):
    dd = env[2]
    root, e = _headpose(env, monkeypatch, [("train", "n_jpg.rf.1", "0 0.5 0.5 0.4 0.4\n"),
                                           ("train", "l_jpg.rf.2", "2 0.5 0.5 0.4 0.4\n")])
    nf = root / "train/images/n_jpg.rf.1.jpg"
    p = dd.plan_roboflow_image(nf, e, _mp(face=False), 100, 100)
    assert p["status"] == "ok" and p["kind"] == "face" and p["head_source"] == "human_box"
    assert p["crop"][2] == pytest.approx(40 * 1.2) and p["n_eye_boxes"] == 0
    monkeypatch.setattr(dd.config, "DET_HEADPOSE_ALLOW_INCOMPLETE_EYES", False)
    assert dd.plan_roboflow_image(nf, e, _mp(face=False), 100, 100)["reason"] == "no_part_boxes"
    # "neither" pose (None): no head box; with landmarks the eyes still carry the image
    q = dd.plan_roboflow_image(root / "train/images/l_jpg.rf.2.jpg", e, _mp(), 100, 100)
    assert q["status"] == "ok" and q["head_class"] == "" and q["head_source"] == "human"


def test_headpose_mouths_from_jaw_open(env, monkeypatch):
    dd = env[2]
    root, e = _headpose(env, monkeypatch, [("train", "y_jpg.rf.1", "1 0.5 0.5 0.7 0.7\n")])
    img = root / "train/images/y_jpg.rf.1.jpg"
    p = dd.plan_roboflow_image(img, e, _mp(jaw_open="0.9"), 100, 100)
    assert p["status"] == "ok" and p["pseudo_yawn"] == 1 and p["n_yawn_boxes"] == 1
    mid = (dd.config.DET_PSEUDO_YAWN_JAW_CLOSED + dd.config.DET_PSEUDO_YAWN_JAW_OPEN) / 2
    assert dd.plan_roboflow_image(img, e, _mp(jaw_open=str(mid)), 100, 100)["reason"] == "ambiguous_unlabelled_mouth"
    # a source that labels its own yawns: clear unboxed yawns are still added, unclear ones kept
    monkeypatch.setitem(dd.config.ROBOFLOW_CLASS_MAP, HP, {"Left": "yawn"})
    assert dd.plan_roboflow_image(img, e, _mp(jaw_open="0.9"), 100, 100)["pseudo_yawn"] == 1
    q = dd.plan_roboflow_image(img, e, _mp(jaw_open=str(mid)), 100, 100)
    assert q["status"] == "ok" and q["pseudo_yawn"] == 0


def test_human_mouth_labels_beat_jaw_open(env, monkeypatch):
    dd = env[2]
    root, e = _headpose(env, monkeypatch, [("train", "m_jpg.rf.1", "1 0.5 0.5 0.7 0.7\n2 0.5 0.7 0.1 0.1\n")])
    img = root / "train/images/m_jpg.rf.1.jpg"     # labels: Forward (head) + Left (as the mouth label)
    monkeypatch.setitem(dd.config.ROBOFLOW_MOUTH_LABELS, HP, {"Left": "closed"})
    assert dd.plan_roboflow_image(img, e, _mp(jaw_open="0.95"), 100, 100)["pseudo_yawn"] == 0
    monkeypatch.setitem(dd.config.ROBOFLOW_MOUTH_LABELS, HP, {"Left": "yawn"})
    p = dd.plan_roboflow_image(img, e, _mp(jaw_open="0.0"), 100, 100)   # mesh closed a wide yawn
    assert p["status"] == "ok" and p["pseudo_yawn"] == 1
    assert dd.plan_roboflow_image(img, e, _mp(face=False), 100, 100)["reason"] == "ambiguous_unlabelled_mouth"
    monkeypatch.setitem(dd.config.ROBOFLOW_MOUTH_LABELS, HP, {"Left": "open"})
    assert dd.plan_roboflow_image(img, e, _mp(jaw_open="0.6"), 100, 100)["reason"] == "ambiguous_unlabelled_mouth"
    monkeypatch.setitem(dd.config.ROBOFLOW_MOUTH_LABELS, HP, {"Down": "yawn", "": "closed"})
    assert dd.plan_roboflow_image(img, e, _mp(jaw_open="0.95"), 100, 100)["pseudo_yawn"] == 0   # default


def test_no_pseudo_eyes_on_turned_faces(env, monkeypatch):
    dd = env[2]
    root, e = _headpose(env, monkeypatch, [("train", "t_jpg.rf.1", "2 0.5 0.5 0.7 0.7\n")])
    p = dd.plan_roboflow_image(root / "train/images/t_jpg.rf.1.jpg", e, _mp(yaw="55"), 100, 100)
    assert p["pseudo_eyes"] == 0 and p["reason"] == "no_part_boxes"     # turned, no head box, no eyes


def test_unclear_eye_kept_only_on_head_down(env, monkeypatch):
    dd = env[2]
    root, e = _headpose(env, monkeypatch, [("train", "d_jpg.rf.1", "0 0.5 0.5 0.7 0.7\n"),
                                           ("train", "f_jpg.rf.2", "1 0.5 0.5 0.7 0.7\n")])
    unclear = _mp(ear_a="0.2")                      # between DET_PSEUDO_EAR_CLOSED and _OPEN
    p = dd.plan_roboflow_image(root / "train/images/d_jpg.rf.1.jpg", e, unclear, 100, 100)
    assert p["status"] == "ok" and p["head_class"] == "head_down" and p["n_eye_boxes"] == 1
    q = dd.plan_roboflow_image(root / "train/images/f_jpg.rf.2.jpg", e, unclear, 100, 100)
    assert q["reason"] == "ambiguous_unlabelled_eye"                    # straight head: rejected


def test_tiny_human_head_label_is_ignored(env, monkeypatch):
    dd = env[2]
    root, e = _headpose(env, monkeypatch, [("train", "t_jpg.rf.1", "0 0.38 0.40 0.06 0.04\n")])
    p = dd.plan_roboflow_image(root / "train/images/t_jpg.rf.1.jpg", e, _mp(), 100, 100)
    assert p["head_class"] == "head_straight" and p["head_source"] == "landmarks"   # not "Down"


def test_pitch_checked_head_labels(env, monkeypatch):
    dd = env[2]
    root, e = _headpose(env, monkeypatch, [("train", "d_jpg.rf.1", "0 0.5 0.5 0.7 0.7\n"),
                                           ("train", "f_jpg.rf.2", "1 0.5 0.5 0.7 0.7\n")])
    monkeypatch.setattr(dd.config, "ROBOFLOW_HEAD_PITCH_CHECKED_PROJECTS", (HP,))
    down, fwd = root / "train/images/d_jpg.rf.1.jpg", root / "train/images/f_jpg.rf.2.jpg"
    floor = dd.config.DET_HUMAN_HEAD_DOWN_MIN_PITCH_DEG
    p = dd.plan_roboflow_image(down, e, _mp(pitch=str(floor)), 100, 100)   # under the 20° cut, kept
    assert p["status"] == "ok" and p["head_class"] == "head_down" and p["head_source"] == "human"
    assert dd.plan_roboflow_image(down, e, _mp(pitch=str(floor - 1)), 100, 100)["reason"] == "head_label_disagrees"
    assert dd.plan_roboflow_image(fwd, e, _mp(pitch="5"), 100, 100)["head_class"] == "head_straight"
    assert dd.plan_roboflow_image(fwd, e, _mp(pitch="30"), 100, 100)["reason"] == "head_label_disagrees"
    assert dd.plan_roboflow_image(fwd, e, _mp(yaw="35"), 100, 100)["reason"] == "head_label_disagrees"
    q = dd.plan_roboflow_image(down, e, _mp(pose=False), 100, 100)          # no landmarks: label stands
    assert q["status"] == "ok" and q["head_class"] == "head_down"
    monkeypatch.setattr(dd.config, "ROBOFLOW_HEAD_PITCH_CHECKED_PROJECTS", ())
    assert dd.plan_roboflow_image(down, e, _mp(pitch="0"), 100, 100)["head_class"] == "head_down"


def test_pitch_check_skipped_in_profile(env, monkeypatch):
    dd = env[2]
    root, e = _headpose(env, monkeypatch, [("train", "d_jpg.rf.1", "0 0.5 0.5 0.7 0.7\n"),
                                           ("train", "f_jpg.rf.2", "1 0.5 0.5 0.7 0.7\n")])
    monkeypatch.setattr(dd.config, "ROBOFLOW_HEAD_PITCH_CHECKED_PROJECTS", (HP,))
    down, fwd = root / "train/images/d_jpg.rf.1.jpg", root / "train/images/f_jpg.rf.2.jpg"
    profile = _mp(pitch="0", yaw="50")
    assert dd.plan_roboflow_image(down, e, profile, 100, 100)["reason"] == "head_label_disagrees"
    monkeypatch.setitem(dd.config.DET_HEAD_CHECK_MAX_YAW_DEG_BY_PROJECT, HP, 30.0)
    assert dd.plan_roboflow_image(down, e, profile, 100, 100)["head_class"] == "head_down"
    assert dd.plan_roboflow_image(down, e, _mp(pitch="0", yaw="20"), 100, 100)["reason"] == "head_label_disagrees"
    # a profile "straight" label is still checked
    assert dd.plan_roboflow_image(fwd, e, profile, 100, 100)["reason"] == "head_label_disagrees"


def test_box_brightness_step(env):
    dd = env[2]
    y, x = np.mgrid[0:200, 0:200]
    clean = (60 + 0.5 * x + 0.3 * y + np.random.default_rng(0).normal(0, 2, (200, 200))).clip(0, 255)
    box = [(0, 0.5, 0.5, 0.4, 0.5)]
    aug = clean.copy()
    aug[50:150, 60:140] *= 1.3                                         # Roboflow "bbbrightness"
    assert dd.box_brightness_step(clean.astype(np.uint8), box) < 3
    assert dd.box_brightness_step(aug.clip(0, 255).astype(np.uint8), box) > 20
    assert np.isnan(dd.box_brightness_step(clean.astype(np.uint8), []))


def test_image_level_head_label(env, monkeypatch):
    dd = env[2]
    # A whole-body box. As a head box, a missed face would be cropped around it (human_box).
    root, e = _headpose(env, monkeypatch, [("train", "b_jpg.rf.1", "0 0.7 0.7 0.6 0.6\n")])
    img = root / "train/images/b_jpg.rf.1.jpg"
    assert dd.plan_roboflow_image(img, e, _mp(face=False), 100, 100)["head_source"] == "human_box"
    monkeypatch.setattr(dd.config, "ROBOFLOW_IMAGE_LEVEL_HEAD_PROJECTS", (HP,))
    p = dd.plan_roboflow_image(img, e, _mp(pitch="30"), 100, 100)
    assert p["status"] == "ok" and p["head_class"] == "head_down" and p["head_source"] == "human"
    heads = [b for c, b in p["boxes"] if c == "head_down"]
    assert heads[0][2] == pytest.approx(60 / 90)                        # the face box, not the body box
    assert dd.plan_roboflow_image(img, e, _mp(face=False), 100, 100)["reason"] == "no_face"
    # A tiny "Down" box is a fine image-level label too: no DET_HUMAN_HEAD_MIN_REL check.
    (root / "train/labels/b_jpg.rf.1.txt").write_text("0 0.1 0.1 0.05 0.05\n")
    assert dd.plan_roboflow_image(img, e, _mp(pitch="30"), 100, 100)["head_source"] == "human"


def test_new_head_down_sources_are_fully_mapped(env):
    dd = env[2]
    # The class lists the 2026-10-26 exports came with; check_head_maps must accept each one.
    classes = {
        "kantuk-edo70": ["eye_closed", "eye_open", "head_dropped", "head_straight", "no_yawn", "yawn"],
        "headmovement": ["-", "bawah", "depan", "kanan", "kiri", "r"],
        "noddingoff-z8zad": ["Distraction"],
    }
    manifest = {"sources": {f"roboflow/{p}": {"kind": "roboflow", "project": p, "class_names": n}
                            for p, n in classes.items()}}
    dd.check_head_maps(manifest)
    assert set(classes) <= set(dd.config.ROBOFLOW_HEADPOSE_PROJECTS)
    assert set(classes) == set(dd.config.ROBOFLOW_HEAD_PITCH_CHECKED_PROJECTS)
    assert {s["project"] for s in dd.config.ROBOFLOW_PROJECTS} >= set(classes)


def test_landmark_only_source(env, monkeypatch):
    paths, external, dd = env
    root, e = _export(paths, external, "some-faces", [("train", "f_jpg.rf.1", "3 0.5 0.5 0.9 0.9\n")])
    monkeypatch.setattr(dd.config, "ROBOFLOW_LANDMARK_ONLY_PROJECTS", ("some-faces",))
    img = root / "train/images/f_jpg.rf.1.jpg"
    p = dd.plan_roboflow_image(img, e, _mp(pitch="30", ear_a="0.1", ear_b="0.1", jaw_open="0.9"), 100, 100)
    assert p["status"] == "ok" and p["head_class"] == "head_down" and p["head_source"] == "landmarks"
    assert sorted(c for c, _ in p["boxes"]) == ["closed_eye", "closed_eye", "head_down", "yawn"]
    assert p["raw_labels"] == "yawn"                                  # recorded, never used
    assert dd.plan_roboflow_image(img, e, _mp(pose=False), 100, 100)["reason"] == "no_landmarks"
    from collections import Counter
    from argus_dataset import parts_dataset
    assert parts_dataset.roboflow_rows(external.load_manifest(), None, Counter(), Counter()) == []


def test_check_head_maps(env, monkeypatch):
    dd = env[2]
    _headpose(env, monkeypatch, [("train", "x_jpg.rf.1", "0 0.5 0.5 0.4 0.4\n")])
    dd.check_head_maps(env[1].load_manifest())                          # fully mapped: fine
    monkeypatch.setitem(dd.config.ROBOFLOW_HEAD_CLASS_MAP, HP, {"Down": "head_down"})
    with pytest.raises(SystemExit, match="'Forward': 'head_straight'"):
        dd.check_head_maps(env[1].load_manifest())
    assert dd.suggest_head_map(["Looking Down", "Front", "Right"]) == {
        "Looking Down": "head_down", "Front": "head_straight", "Right": None}


def test_label_sheet(env, monkeypatch):
    dd = env[2]
    _headpose(env, monkeypatch, [("train", f"s{i}_jpg.rf.1", f"{i % 3} 0.5 0.5 0.4 0.4\n")
                                 for i in range(6)])
    sheet = cv2.imread(str(dd.label_sheet(HP, per_class=4)))
    assert sheet.shape[:2] == (3 * 160, 4 * 160)


def test_four_corner_polygons_read_as_boxes(env, tmp_path):
    from argus_dataset import parts_dataset
    lbl = tmp_path / "x.txt"   # headpose-w4zuc writes every box as a 4-corner polygon
    lbl.write_text("5 0.1 0.2 0.9 0.2 0.9 0.8 0.1 0.8\n1 0.5 0.5 0.2 0.1\n"
                   "2 0.1 0.1 0.2 0.1 0.2 0.2 0.15 0.25 0.1 0.2\n")   # 5-point polygon: skipped
    boxes, polys = parts_dataset._read_yolo_labels(lbl)
    assert boxes[0] == pytest.approx((5, 0.5, 0.5, 0.8, 0.6)) and boxes[1] == (1, 0.5, 0.5, 0.2, 0.1)
    assert len(boxes) == 2 and polys == 1


def test_parts_dataset_skips_headpose_projects(env, monkeypatch):
    from collections import Counter
    _headpose(env, monkeypatch, [("train", "x_jpg.rf.1", "0 0.5 0.5 0.4 0.4\n")])
    from argus_dataset import parts_dataset
    assert parts_dataset.roboflow_rows(env[1].load_manifest(), None, Counter(), Counter()) == []


# --- balance report --------------------------------------------------------------------------

def test_balance_report_counts(env, tmp_path):
    import pandas as pd
    from argus_dataset import det_report
    index = pd.DataFrame([
        {"image_id": "a", "status": "ok", "split": "train", "source": "roboflow", "project": "p1", "group_id": "g1"},
        {"image_id": "b", "status": "ok", "split": "train", "source": "roboflow", "project": "p2", "group_id": "g1"},
        {"image_id": "c", "status": "ok", "split": "test", "source": "mrl_eye", "project": "mrl_eye", "group_id": "m1"},
        {"image_id": "d", "status": "rejected", "split": "valid", "source": "roboflow", "project": "p1", "group_id": "g2"},
    ])
    labels = {"a": ["open_eye", "open_eye", "head_down"], "b": ["open_eye"], "c": ["closed_eye"],
              "d": ["yawn"]}
    counts = det_report.count_labels(index, labels)
    boxes, images, groups = (det_report.per_split(counts, m) for m in ("boxes", "images", "groups"))
    assert boxes.loc["open_eye", "train"] == 3 and images.loc["open_eye", "train"] == 2
    assert groups.loc["open_eye", "train"] == 1          # one group across two sources, counted once
    assert boxes.loc["yawn"].sum() == 0                   # rejected rows never count
    assert list(boxes.index) == list(det_report.config.DET_CLASSES)
    assert list(boxes.columns) == ["train", "valid", "test"]
    src = det_report.per_source(counts)
    assert src.loc["mrl_eye", "closed_eye"] == 1 and src.index[0] == "p1"
    assert det_report.summary_table(counts).loc["open_eye", ("groups_pct", "train")] == 100.0
    lbl = tmp_path / "x.txt"
    lbl.write_text("4 0.5 0.5 0.2 0.2\n0 0.3 0.3 0.1 0.1\n")
    assert det_report.read_labels(lbl) == ["head_down", "open_eye"]
