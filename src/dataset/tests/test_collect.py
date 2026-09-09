"""collect.py — subject/clip naming (the no-overwrite guarantee), import, provenance log."""

import importlib

import pytest


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("ARGUS_DATASET_ROOT", str(tmp_path))
    from argus_dataset import paths
    importlib.reload(paths)
    from argus_dataset import collect
    importlib.reload(collect)
    paths.ensure_dirs()
    return paths, collect


# --- naming -------------------------------------------------------------------------------

@pytest.mark.parametrize("raw,expected", [
    ("7", "subject_07"), ("07", "subject_07"), ("subject_7", "subject_07"),
    ("subject_07", "subject_07"), ("SUBJECT_42", "subject_42"), ("123", "subject_123"),
])
def test_normalize_subject(env, raw, expected):
    _, collect = env
    assert collect.normalize_subject(raw) == expected


def test_normalize_subject_rejects_garbage(env):
    _, collect = env
    with pytest.raises(ValueError):
        collect.normalize_subject("driver-x")


def test_next_subject_id(env):
    paths, collect = env
    assert collect.next_subject_id() == "subject_01"
    (paths.raw_dir() / "subject_03").mkdir()
    (paths.raw_dir() / "subject_54").mkdir()
    (paths.raw_dir() / "not_a_subject").mkdir()
    assert collect.next_subject_id() == "subject_55"


def test_resolve_subject_new(env):
    paths, collect = env
    (paths.raw_dir() / "subject_09").mkdir()
    assert collect.resolve_subject("new") == "subject_10"
    assert collect.resolve_subject("7") == "subject_07"


def test_next_clip_number_and_no_overwrite(env):
    paths, collect = env
    sub = paths.raw_dir() / "subject_01"
    sub.mkdir()
    assert collect.next_clip_number(sub, 1) == 1

    (sub / "level_1_clip_01.mp4").write_bytes(b"x")
    (sub / "level_1_clip_02.mp4").write_bytes(b"x")
    (sub / "level_2_clip_01.mp4").write_bytes(b"x")
    assert collect.next_clip_number(sub, 1) == 3
    assert collect.next_clip_number(sub, 2) == 2  # level counted separately

    with pytest.raises(FileExistsError):
        collect.clip_path("subject_01", 1, 1)
    assert collect.clip_path("subject_01", 1, 3).name == "level_1_clip_03.mp4"


# --- import + probe -----------------------------------------------------------------------

def _synth_mp4(path, n_frames=15, w=64, h=48, fps=15.0):
    import cv2
    import numpy as np

    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), fps, (w, h))
    if not writer.isOpened():
        return False
    for i in range(n_frames):
        writer.write(np.full((h, w, 3), i * 7 % 256, dtype=np.uint8))
    writer.release()
    return path.exists() and path.stat().st_size > 0


def test_import_file_copies_verbatim(env, tmp_path):
    paths, collect = env
    src = tmp_path / "phone.mp4"
    if not _synth_mp4(src):
        pytest.skip("no mp4v VideoWriter in this OpenCV build")

    (paths.raw_dir() / "subject_01").mkdir()
    dest = collect.clip_path("subject_01", 2, 1)
    info = collect.import_file(src, dest)

    assert dest.exists()
    assert dest.read_bytes() == src.read_bytes()  # verbatim copy
    assert info.n_frames >= 10
    assert info.source == str(src.resolve())


def test_probe_video_rejects_nonvideo(env, tmp_path):
    _, collect = env
    bad = tmp_path / "not.mp4"
    bad.write_bytes(b"not a video")
    with pytest.raises(IOError):
        collect.probe_video(bad)


# --- provenance log ----------------------------------------------------------------------

def test_append_collection_log(env):
    import csv as _csv

    paths, collect = env
    for n in (1, 2):
        info = collect.ClipInfo(
            path=str(paths.raw_dir() / "subject_01" / f"level_1_clip_0{n}.mp4"),
            subject="subject_01", level=1, clip_number=n, source="webcam:0",
            n_frames=300, fps=15.0, duration_sec=20.0, width=640, height=480,
        )
        collect.append_collection_log(info)

    log = paths.collection_log_csv()
    rows = list(_csv.DictReader(log.read_text().splitlines()))
    assert len(rows) == 2
    assert rows[0]["subject"] == "subject_01"
    assert rows[1]["clip_number"] == "2"
    assert rows[0]["filename"] == "level_1_clip_01.mp4"


def test_inventory(env):
    paths, collect = env
    s1 = paths.raw_dir() / "subject_01"
    s1.mkdir()
    (s1 / "level_1_clip_01.mp4").write_bytes(b"x")
    (s1 / "level_1_clip_02.mp4").write_bytes(b"x")
    (s1 / "level_2_clip_01.mp4").write_bytes(b"x")
    assert collect.inventory() == [("subject_01", 2, 1)]
