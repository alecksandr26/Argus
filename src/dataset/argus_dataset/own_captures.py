"""Our own guided captures (``frames.py``, ``raw/webcam_frames/``) → a head-pose source for the
detection dataset, labelled without Roboflow.

The capture tag is what the person was asked to do, so it can't be a label on its own (people
start a pose late, blink, look away). It becomes one only where the landmarks agree:

- a still tagged head-down (``config.OWN_HEAD_DOWN_TAGS``) gets a human ``head_down`` box (the
  BlazeFace face box, the scope of every head box in the dataset) when its pitch is at least
  ``config.OWN_HEAD_DOWN_MIN_PITCH_DEG`` or FaceLandmarker lost the face; below that it's dropped;
- every other still gets an empty label file: eyes, yawns and the head come from the landmarks in
  ``detection_dataset.plan_roboflow_image``, like any head-pose source.

The output is a Roboflow-style export, ``raw/roboflow/own-captures/v1/train/{images,labels}``,
registered in ``datasets_manifest.json``, so ``build_detection_dataset.py`` picks it up with no
special case. Files are named ``<subject>_jpg.rf.<hash>.jpg``: ``parts_dataset.original_stem``
then gives the subject, so every still of one person forms one group and never straddles splits.
The folder is rebuilt from scratch on each run.
"""
from __future__ import annotations

import csv
import hashlib
import shutil
from collections import Counter, defaultdict
from pathlib import Path
from typing import Callable

import numpy as np

from . import config, external, frames, paths

CLASS_NAMES = ["head_down"]     # class 0 of the label files; mapped in ROBOFLOW_HEAD_CLASS_MAP


def project_dir() -> Path:
    return paths.roboflow_raw_dir() / config.OWN_CAPTURES_PROJECT / "v1"


def manifest_key() -> str:
    return f"roboflow/{config.OWN_CAPTURES_PROJECT}"


def plan_still(tag: str, mp_row: dict, sharpness: float | None = None, min_sharpness: float = 0.0,
               min_pitch: float = config.OWN_HEAD_DOWN_MIN_PITCH_DEG) -> tuple[str, str]:
    """``(decision, reason)``: decision is ``"head_down"`` (write a head_down box), ``"auto"``
    (empty label, the landmarks decide) or ``"skip"``. Pure, so it's unit-tested directly."""
    if sharpness is not None and sharpness < min_sharpness:
        return "skip", "blurry"
    if str(mp_row.get("face_found")) != "1":
        return "skip", "no_face"
    if tag not in config.OWN_HEAD_DOWN_TAGS:
        return "auto", ""
    if str(mp_row.get("landmarks_found")) != "1":
        return "head_down", "no_landmarks"
    if config.HEAD_DOWN_PITCH_SIGN * float(mp_row["pitch"]) < min_pitch:
        return "skip", "not_down"
    return "head_down", "pitch"


def face_label_line(mp_row: dict, W: int, H: int) -> str:
    """The BlazeFace face box as a YOLO line of class 0 (``head_down``)."""
    fx, fy, fw, fh = (float(mp_row[k]) for k in ("fx", "fy", "fw", "fh"))
    return f"0 {(fx + fw / 2) / W:.6f} {(fy + fh / 2) / H:.6f} {fw / W:.6f} {fh / H:.6f}"


def out_name(subject: str, session: str, filename: str) -> str:
    h = hashlib.sha1(f"{subject}/{session}/{filename}".encode()).hexdigest()[:12]
    return f"{subject}_jpg.rf.{h}.jpg"


def iter_stills(root: Path | None = None):
    """Every still listed in a session's ``frames.csv`` whose file still exists, as
    ``(path, subject, session, tag, sharpness)``."""
    root = root or paths.frames_raw_dir()
    for csv_path in sorted(root.glob(f"*/*/{frames.FRAMES_CSV}")):
        with open(csv_path, newline="") as f:
            for r in csv.DictReader(f):
                p = csv_path.parent / r["filename"]
                if p.exists():
                    s = r.get("sharpness")
                    yield p, r["subject"], r["session"], r["tag"], float(s) if s else None


def pitch_summary(pitches: dict[str, list[float]]) -> dict[str, dict]:
    """Per tag: count and 5/50/95th percentile of the down-positive pitch, for choosing
    ``--min-pitch`` on a new camera: the straight tags' p95 should sit below it."""
    out = {}
    for tag, ps in sorted(pitches.items()):
        if ps:
            q = np.percentile(ps, [5, 50, 95])
            out[tag] = {"n": len(ps), "p5": round(float(q[0]), 1), "p50": round(float(q[1]), 1),
                        "p95": round(float(q[2]), 1)}
    return out


def export(mp_fn: Callable[[str], dict], min_sharpness: float = 0.0,
           min_pitch: float = config.OWN_HEAD_DOWN_MIN_PITCH_DEG, progress=lambda it, **k: it) -> dict:
    """Rebuild ``project_dir()`` from every captured still and register it in the manifest (or
    drop the manifest entry when nothing is kept). ``mp_fn`` is ``detection_dataset.mp_one``
    after ``_mp_init``; tests pass a stub."""
    import cv2
    dest = project_dir()
    shutil.rmtree(dest, ignore_errors=True)
    (dest / "train" / "images").mkdir(parents=True)
    (dest / "train" / "labels").mkdir(parents=True)
    by_tag, by_subject, pitches = Counter(), Counter(), defaultdict(list)
    kept = n_down = 0
    for p, subject, session, tag, sharp in progress(list(iter_stills()), desc="own captures"):
        mp_row = mp_fn(str(p))
        decision, reason = plan_still(tag, mp_row, sharp, min_sharpness, min_pitch)
        if str(mp_row.get("landmarks_found")) == "1":
            pitches[tag].append(config.HEAD_DOWN_PITCH_SIGN * float(mp_row["pitch"]))
        by_tag[f"{tag}: {decision}{' (' + reason + ')' if reason else ''}"] += 1
        if decision == "skip":
            continue
        img = cv2.imread(str(p))
        if img is None:
            continue
        name = out_name(subject, session, p.name)
        shutil.copyfile(p, dest / "train" / "images" / name)
        line = face_label_line(mp_row, img.shape[1], img.shape[0]) if decision == "head_down" else ""
        (dest / "train" / "labels" / (Path(name).stem + ".txt")).write_text(line + ("\n" if line else ""))
        kept += 1
        n_down += decision == "head_down"
        by_subject[subject] += 1

    manifest = external.load_manifest()
    if kept:
        n_files, fp = external.tree_fingerprint(dest)
        manifest["sources"].pop(manifest_key(), None)
        external.save_manifest(manifest)
        external.upsert_source(manifest_key(), {
            "kind": "roboflow", "project": config.OWN_CAPTURES_PROJECT, "workspace": "local",
            "version": 1, "class_names": CLASS_NAMES, "location": dest.relative_to(paths.dataset_root()).as_posix(),
            "licence": "own (consented subjects)", "images": kept, "splits": {"train": kept},
            "boxes_per_class": {"head_down": n_down}, "tree_sha256": fp, "status": "ok", "url": "",
            "note": "scripts/add_own_captures.py: guided captures, head_down from tag + pitch agreement"})
    elif manifest["sources"].pop(manifest_key(), None) is not None:
        external.save_manifest(manifest)
    return {"kept": kept, "head_down_boxes": n_down, "subjects": dict(by_subject),
            "by_tag": dict(sorted(by_tag.items())), "pitch_by_tag": pitch_summary(pitches)}
