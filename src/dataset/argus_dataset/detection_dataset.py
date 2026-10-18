"""One YOLO-format detection dataset for every detector notebook (12 tiny grid detector,
13 YOLO nano, 14 backbone + grid head).

Images are **square face crops**, the input the edge will see after BlazeFace. Each one gets YOLO
labels for ``config.DET_CLASSES``:
  * ``open_eye`` / ``closed_eye`` / ``yawn``: the Roboflow part boxes merged through
    ``ROBOFLOW_CLASS_MAP``, mapped into face-crop coordinates;
  * ``head_straight`` / ``head_down``: one box on the face, auto-labelled from FaceLandmarker pitch.

MRL eye photos and Roboflow eye/mouth close-ups (no face) go in as ``kind=closeup`` with
full-image boxes and no head box.

Two passes:
  1. **MediaPipe pass** (slow, cached, resumable): BlazeFace on each Roboflow image, then
     FaceLandmarker (IMAGE mode) on its face crop gives pitch/yaw/roll. Cached per chunk under
     ``processed/.cache/det_mp/``.
  2. **Build** (cheap, always rebuilt): crop, map boxes, head labels, reject rules, group splits,
     gray + IR variants, then ``processed/parts_det/`` and ``processed/parts_det.zip``.

Output layout (Ultralytics-compatible; both variants share identical labels):

    gray/{train,valid,test}/{images,labels}/   ir/{...}/   data_gray.yaml  data_ir.yaml
    index.csv  (one row per source image, incl. rejected ones + the reason)   README.txt
"""

from __future__ import annotations

import csv
import hashlib
import os
import random
import zipfile
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor
from multiprocessing import get_context
from pathlib import Path

import numpy as np

from . import config, external, ir, parts_dataset, paths

INDEX_COLUMNS = [
    "image_id", "status", "reason", "kind", "source", "project", "licence", "group_id", "split",
    "src_image", "face_x0", "face_y0", "face_side", "pitch", "yaw", "roll", "head_class",
    "n_eye_boxes", "n_yawn_boxes", "pseudo_eyes", "raw_labels", "gray_path", "ir_path", "label_path",
]
MP_COLUMNS = ["src_image", "face_found", "fx", "fy", "fw", "fh", "face_score",
              "landmarks_found", "pitch", "yaw", "roll",
              # per eye (image-left "a" = landmark 33 side, image-right "b" = 263 side): contour
              # box in normalised face-crop coords + EAR, for pseudo-labelling unlabelled eyes.
              "a_x0", "a_y0", "a_x1", "a_y1", "a_ear", "b_x0", "b_y0", "b_x1", "b_y1", "b_ear"]
# MediaPipe FaceMesh eye contours (16 points each).
_EYE_CONTOUR = {
    "a": (33, 7, 163, 144, 145, 153, 154, 155, 133, 173, 157, 158, 159, 160, 161, 246),
    "b": (362, 382, 381, 380, 374, 373, 390, 249, 263, 466, 388, 387, 386, 385, 384, 398),
}
_EYE_EAR_IDX = {"a": config.LEFT_EYE_EAR_IDX, "b": config.RIGHT_EYE_EAR_IDX}


# --- pure geometry -------------------------------------------------------------------------

def square_face_box(fx, fy, fw, fh, margin=config.DET_FACE_MARGIN_FRAC):
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


def map_box_to_crop(box, W, H, x0, y0, side):
    """YOLO-normalised box in the source image → YOLO-normalised box in the square crop, or None
    if its centre is outside, too little of it is visible, or it ends up too thin."""
    xc, yc, w, h = box
    bx0, by0 = (xc - w / 2) * W, (yc - h / 2) * H
    bx1, by1 = (xc + w / 2) * W, (yc + h / 2) * H
    cx, cy = (bx0 + bx1) / 2, (by0 + by1) / 2
    if not (x0 <= cx <= x0 + side and y0 <= cy <= y0 + side):
        return None
    cx0, cy0 = max(bx0, x0), max(by0, y0)
    cx1, cy1 = min(bx1, x0 + side), min(by1, y0 + side)
    full = max(1e-9, (bx1 - bx0) * (by1 - by0))
    if (cx1 - cx0) * (cy1 - cy0) / full < config.DET_MIN_VISIBLE_FRAC:
        return None
    nw, nh = (cx1 - cx0) / side, (cy1 - cy0) / side
    if min(nw, nh) < config.DET_MIN_BOX_FRAC:
        return None
    return ((cx0 + cx1) / 2 - x0) / side, ((cy0 + cy1) / 2 - y0) / side, nw, nh


def head_class(pitch: float, yaw: float) -> str | None:
    down = config.HEAD_DOWN_PITCH_SIGN * pitch
    if down >= config.HEAD_DOWN_PITCH_DEG:
        return "head_down"
    if abs(pitch) <= config.HEAD_STRAIGHT_PITCH_DEG and abs(yaw) <= config.HEAD_STRAIGHT_YAW_DEG:
        return "head_straight"
    return None


def pseudo_eye_boxes(mp_row: dict, mapped: list) -> tuple[list, bool]:
    """Landmark-based boxes for eyes no human box covers. Returns (new boxes, ambiguous): an
    unlabelled eye whose EAR falls between the open/closed thresholds makes the image ambiguous."""
    new, ambiguous = [], False
    labelled = [b for c, b in mapped if c in ("open_eye", "closed_eye")]
    for k in ("a", "b"):
        x0, y0, x1, y1 = (float(mp_row[f"{k}_{c}"]) for c in ("x0", "y0", "x1", "y1"))
        cx, cy, lw = (x0 + x1) / 2, (y0 + y1) / 2, max(x1 - x0, 1e-6)
        if any(np.hypot(b[0] - cx, b[1] - cy) <= config.DET_PSEUDO_MATCH_DIST * lw
               or (abs(cx - b[0]) <= b[2] / 2 and abs(cy - b[1]) <= b[3] / 2)  # inside a human box
               for b in labelled):
            continue
        ear = float(mp_row[f"{k}_ear"])
        if ear < config.DET_PSEUDO_EAR_CLOSED:
            cls = "closed_eye"
        elif ear > config.DET_PSEUDO_EAR_OPEN:
            cls = "open_eye"
        else:
            ambiguous = True
            continue
        w = lw * config.DET_PSEUDO_EYE_W_SCALE
        h = w * config.DET_PSEUDO_EYE_ASPECT
        if 0 <= cx <= 1 and 0 <= cy <= 1:
            new.append((cls, (cx, cy, min(w, 1.0), min(h, 1.0))))
    return new, ambiguous


def thumb(gray: np.ndarray, size: int = 16) -> np.ndarray:
    import cv2
    t = cv2.resize(gray, (size, size), interpolation=cv2.INTER_AREA).astype(np.float32)
    t -= t.mean()
    return t / (np.linalg.norm(t) + 1e-6)


def thumb_similarity(a: np.ndarray, b: np.ndarray) -> float:
    """Flip-invariant normalised correlation of two ``thumb``s (Roboflow augments with flips)."""
    return float(max((a * b).sum(), (a * b[:, ::-1]).sum()))


def yolo_line(cls: str, box) -> str:
    return f"{config.DET_CLASSES.index(cls)} " + " ".join(f"{v:.6f}" for v in box)


# --- pass 1: MediaPipe (spawned workers, chunk-cached) -------------------------------------

_W = {}


def _mp_init(face_model: str, lm_model: str) -> None:
    import argus_dataset.bootstrap  # noqa: F401  (thread pinning in the worker)
    import cv2
    from . import pipelines
    cv2.setNumThreads(0)
    _W["det"] = pipelines._make_detector(face_model, "IMAGE", config.CROP_MIN_DETECTION_CONFIDENCE)
    _W["lm"] = pipelines._make_landmarker(lm_model, "IMAGE")


def _mp_image(bgr):
    import cv2
    import mediapipe as mp
    rgb = np.ascontiguousarray(cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB))
    return mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)


def mp_one(img_path: str) -> dict:
    """BlazeFace on the full image, then FaceLandmarker on the square face crop → pose."""
    import cv2
    from . import geometry, pipelines
    row = dict.fromkeys(MP_COLUMNS, "")
    row.update(src_image=img_path, face_found=0, landmarks_found=0)
    img = cv2.imread(img_path)
    if img is None:
        return row
    r = _W["det"].detect(_mp_image(img))
    if not r.detections:
        return row
    d = max(r.detections, key=lambda d: d.categories[0].score)
    b = d.bounding_box
    row.update(face_found=1, fx=b.origin_x, fy=b.origin_y, fw=b.width, fh=b.height,
               face_score=round(d.categories[0].score, 4))
    x0, y0, side = square_face_box(b.origin_x, b.origin_y, b.width, b.height)
    crop = crop_square(img, x0, y0, side, config.DET_IMG_SIZE)
    out = pipelines._landmarks_and_matrix(_W["lm"].detect(_mp_image(crop)))
    if out is not None:
        lms, R = out
        pitch, yaw, roll = (float(v) for v in geometry._rotation_matrix_to_euler(R))
        row.update(landmarks_found=1, pitch=round(pitch, 3), yaw=round(yaw, 3), roll=round(roll, 3))
        for eye, contour in _EYE_CONTOUR.items():
            pts = lms[list(contour)]
            (x0, y0), (x1, y1) = pts.min(axis=0), pts.max(axis=0)
            ear = float(geometry._ear(lms, np.asarray(_EYE_EAR_IDX[eye])))
            row.update({f"{eye}_x0": round(float(x0), 5), f"{eye}_y0": round(float(y0), 5),
                        f"{eye}_x1": round(float(x1), 5), f"{eye}_y1": round(float(y1), 5),
                        f"{eye}_ear": round(ear, 4)})
    return row


def _mp_chunk(chunk_paths: list[str], dest: str) -> int:
    rows = [mp_one(p) for p in chunk_paths]
    tmp = dest + ".tmp"
    with open(tmp, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=MP_COLUMNS)
        w.writeheader()
        w.writerows(rows)
    os.replace(tmp, dest)
    return len(rows)


def mp_cache_dir() -> Path:
    return paths.cache_dir() / "det_mp"


def roboflow_images(manifest: dict) -> list[Path]:
    out = []
    for _, e in sorted(manifest["sources"].items()):
        if e.get("kind") == "roboflow":
            root = paths.dataset_root() / e["location"]
            out += sorted(p for p in root.glob("*/images/*") if p.suffix.lower() in external.IMAGE_EXTS)
    return out


def run_mediapipe_pass(images: list[Path], workers: int, progress=lambda it, **k: it) -> dict:
    """Process ``images`` in deterministic chunks; a chunk whose cache file exists is skipped,
    so Ctrl-C + rerun resumes. Chunk files are keyed by a hash of their paths."""
    cache = mp_cache_dir()
    cache.mkdir(parents=True, exist_ok=True)
    paths_s = [str(p) for p in images]
    chunks = [paths_s[i:i + config.DET_MP_CHUNK] for i in range(0, len(paths_s), config.DET_MP_CHUNK)]
    todo = []
    for c in chunks:
        dest = cache / f"{hashlib.sha1(''.join(c).encode()).hexdigest()[:16]}.csv"
        if not dest.exists():
            todo.append((c, str(dest)))
    print(f"MediaPipe pass: {len(chunks)} chunks, {len(chunks) - len(todo)} cached, {len(todo)} to run "
          f"({workers} workers)")
    if todo:
        ctx = get_context("spawn")
        with ProcessPoolExecutor(max_workers=workers, mp_context=ctx, initializer=_mp_init,
                                 initargs=(str(paths.face_detector_path()),
                                           str(paths.face_landmarker_path()))) as ex:
            futs = [ex.submit(_mp_chunk, c, d) for c, d in todo]
            for f in progress(futs, desc="mediapipe chunks"):
                f.result()
    return load_mp_cache()


def load_mp_cache() -> dict[str, dict]:
    out = {}
    for f in sorted(mp_cache_dir().glob("*.csv")):
        with open(f, newline="") as fh:
            for r in csv.DictReader(fh):
                out[r["src_image"]] = r
    return out


# --- pass 2: build -------------------------------------------------------------------------

def _f(v):
    return float(v) if v not in ("", None) else None


def _rel(p) -> str:
    return Path(p).resolve().relative_to(paths.dataset_root().resolve()).as_posix()


def plan_roboflow_image(img_path: Path, entry: dict, mp_row: dict | None, W: int, H: int) -> dict:
    """Decide everything about one Roboflow image without touching pixels: kind, crop square,
    mapped boxes, head class, accept/reject. Pure, so it's unit-tested directly."""
    project, names = entry["project"], entry["class_names"]
    cmap = config.ROBOFLOW_CLASS_MAP[project]
    reject_labels = set(config.ROBOFLOW_REJECT_IMAGE_LABELS.get(project, ()))
    lbl = img_path.parent.parent / "labels" / (img_path.stem + ".txt")
    boxes, _ = parts_dataset._read_yolo_labels(lbl) if lbl.exists() else ([], 0)
    raw = [names[c] if c < len(names) else f"id_{c}" for c, *_ in boxes]
    plan = {"raw_labels": "|".join(sorted(set(raw))), "status": "rejected", "reason": "",
            "kind": "", "boxes": [], "head_class": "", "crop": None}
    if reject_labels & set(raw):
        plan["reason"] = "ambiguous_label:" + ",".join(sorted(reject_labels & set(raw)))
        return plan
    parts = [(cmap[n], tuple(b)) for (c, *b), n in zip(boxes, raw) if cmap.get(n)]
    face = mp_row and str(mp_row.get("face_found")) == "1"
    if face:
        x0, y0, side = square_face_box(*(float(mp_row[k]) for k in ("fx", "fy", "fw", "fh")))
        mapped = [(cls, m) for cls, b in parts if (m := map_box_to_crop(b, W, H, x0, y0, side))]
        plan.update(kind="face", crop=(x0, y0, side))
        if str(mp_row.get("landmarks_found")) == "1" and config.DET_PSEUDO_EYES:
            pseudo, ambiguous = pseudo_eye_boxes(mp_row, mapped)
            if ambiguous:
                plan["reason"] = "ambiguous_unlabelled_eye"
                return plan
            mapped += pseudo
            plan["pseudo_eyes"] = len(pseudo)
        if str(mp_row.get("landmarks_found")) == "1":
            hc = head_class(float(mp_row["pitch"]), float(mp_row["yaw"]))
            if hc:
                face_box = map_box_to_crop(
                    ((float(mp_row["fx"]) + float(mp_row["fw"]) / 2) / W,
                     (float(mp_row["fy"]) + float(mp_row["fh"]) / 2) / H,
                     float(mp_row["fw"]) / W, float(mp_row["fh"]) / H), W, H, x0, y0, side)
                if face_box:
                    mapped.append((hc, face_box))
                    plan["head_class"] = hc
    elif parts and all(max(b[2], b[3]) >= config.DET_CLOSEUP_MIN_BOX for _, b in parts):
        side = max(W, H)
        x0, y0 = (W - side) / 2, (H - side) / 2
        mapped = [(cls, m) for cls, b in parts if (m := map_box_to_crop(b, W, H, x0, y0, side))]
        plan.update(kind="closeup", crop=(x0, y0, side))
    else:
        plan["reason"] = "no_face"
        return plan
    plan["boxes"] = mapped
    n_eye = sum(c in ("open_eye", "closed_eye") for c, _ in mapped)
    n_yawn = sum(c == "yawn" for c, _ in mapped)
    plan.update(n_eye_boxes=n_eye, n_yawn_boxes=n_yawn)
    if not any(c in ("open_eye", "closed_eye", "yawn") for c, _ in mapped):
        plan["reason"] = "no_part_boxes"
    elif plan["kind"] == "face" and config.DET_REQUIRE_BOTH_EYES and n_eye < 2:
        plan["reason"] = "eyes_incomplete"
    else:
        plan["status"] = "ok"
    return plan


def _write_pair(base: Path, split: str, image_id: str, gray: np.ndarray, irimg: np.ndarray,
                lines: list[str]) -> tuple[str, str, str]:
    import cv2
    out = []
    for variant, im in (("gray", gray), ("ir", irimg)):
        ip = base / variant / split / "images" / f"{image_id}.jpg"
        lp = base / variant / split / "labels" / f"{image_id}.txt"
        ip.parent.mkdir(parents=True, exist_ok=True)
        lp.parent.mkdir(parents=True, exist_ok=True)
        if not ip.exists():
            cv2.imwrite(str(ip), im, [cv2.IMWRITE_JPEG_QUALITY, config.DET_JPEG_QUALITY])
        lp.write_text("\n".join(lines) + ("\n" if lines else ""))
        out.append(ip.relative_to(base).as_posix())
    return out[0], out[1], (base / "gray" / split / "labels" / f"{image_id}.txt").relative_to(base).as_posix()


def _mrl_sample(manifest: dict, seed: int = 0) -> list[dict]:
    e = manifest["sources"].get("mrl_eye")
    if not e:
        return []
    root = paths.dataset_root() / e["location"]
    by = defaultdict(list)
    for p in sorted(root.rglob("*.png")):
        d = external.parse_mrl_filename(p.name)
        if d:
            by[(d["subject"], d["eye_state"])].append(p)
    rng = random.Random(seed)
    out = []
    for (subj, state), ps in sorted(by.items()):
        for p in rng.sample(ps, min(len(ps), config.DET_MRL_MAX_PER_SUBJECT_CLASS)):
            out.append({"path": p, "subject": subj,
                        "cls": "open_eye" if state == "open" else "closed_eye",
                        "licence": e.get("licence", "")})
    return out


def build(mp_cache: dict, progress=lambda it, **k: it) -> dict:
    import cv2
    manifest = external.load_manifest()
    base = paths.processed_dir() / "parts_det"
    mrl = manifest["sources"].get("mrl_eye")
    ref_cdf = (np.load(paths.ir_reference_hist()) if paths.ir_reference_hist().exists() else
               (parts_dataset.build_ir_reference(paths.dataset_root() / mrl["location"]) if mrl else None))

    # Plan every Roboflow image first (needs size only) so groups/splits exist before writing.
    # Grouping (leakage guard), learned the hard way on this data:
    #  * Roboflow augmented copies share an original stem (<orig>_jpg.rf.<hash>) — but merged
    #    projects (DET_STEM_UNRELIABLE_PROJECTS) reuse stems for UNRELATED images ("1.jpg" from
    #    several sources); there stem-mates join only if their thumbnails match.
    #  * dHash joins exact re-uploads ACROSS projects (forks) only — within a project, consecutive
    #    video frames share hashes and chained thousands of images into one group.
    rows, plans, uf, hash_owner = [], {}, parts_dataset._UnionFind(), {}
    buckets = defaultdict(list)
    for key, e in sorted(manifest["sources"].items()):
        if e.get("kind") != "roboflow":
            continue
        root = paths.dataset_root() / e["location"]
        imgs = sorted(p for p in root.glob("*/images/*") if p.suffix.lower() in external.IMAGE_EXTS)
        for p in progress(imgs, desc=f"plan {e['project']}"):
            img = cv2.imread(str(p))
            if img is None:
                continue
            H, W = img.shape[:2]
            plan = plan_roboflow_image(p, e, mp_cache.get(str(p)), W, H)
            image_id = f"{e['project']}__{p.stem}"
            gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
            owners = hash_owner.setdefault(parts_dataset.dhash(gray), {})
            for proj, other in owners.items():
                if proj != e["project"]:
                    uf.union(image_id, other)
            owners.setdefault(e["project"], image_id)
            uf.find(image_id)
            buckets[(e["project"], parts_dataset.original_stem(p.name))].append((image_id, thumb(gray)))
            plans[image_id] = (p, e, plan, image_id)
    for (project, _), members in buckets.items():
        if project not in config.DET_STEM_UNRELIABLE_PROJECTS:
            for iid, _ in members[1:]:
                uf.union(members[0][0], iid)
            continue
        for i in range(len(members)):
            for j in range(i + 1, len(members)):
                if thumb_similarity(members[i][1], members[j][1]) >= config.DET_SAME_ORIGINAL_CORR:
                    uf.union(members[i][0], members[j][0])

    rf_groups = {iid: "rf/" + uf.find(iid) for iid in plans}
    # Stratify: yawn-bearing groups are rare and clustered (long video sequences), so split them
    # separately from the rest; otherwise valid/test can end up with only a handful of yawns.
    yawn_groups = {rf_groups[iid] for iid, (_, _, plan, _) in plans.items()
                   if plan["status"] == "ok" and plan.get("n_yawn_boxes", 0) > 0}
    rf_split = {**parts_dataset.assign_splits(set(rf_groups.values()) - yawn_groups),
                **parts_dataset.assign_splits(yawn_groups)}
    mrl_items = _mrl_sample(manifest)
    mrl_split = parts_dataset.assign_splits({f"mrl/s{m['subject']:04d}" for m in mrl_items})

    for iid, (p, e, plan, _) in progress(list(plans.items()), desc="write roboflow"):
        g = rf_groups[iid]
        split = rf_split[g]
        mp_row = mp_cache.get(str(p)) or {}
        row = {"image_id": iid, "status": plan["status"], "reason": plan["reason"],
               "kind": plan["kind"], "source": "roboflow", "project": e["project"],
               "licence": e.get("licence", ""), "group_id": g, "split": split,
               "src_image": _rel(p), "pitch": mp_row.get("pitch", ""), "yaw": mp_row.get("yaw", ""),
               "roll": mp_row.get("roll", ""), "head_class": plan["head_class"],
               "n_eye_boxes": plan.get("n_eye_boxes", ""), "n_yawn_boxes": plan.get("n_yawn_boxes", ""),
               "pseudo_eyes": plan.get("pseudo_eyes", 0),
               "raw_labels": plan["raw_labels"]}
        if plan["crop"]:
            row.update(face_x0=round(plan["crop"][0], 1), face_y0=round(plan["crop"][1], 1),
                       face_side=round(plan["crop"][2], 1))
        if plan["status"] == "ok":
            img = cv2.imread(str(p))
            crop = crop_square(img, *plan["crop"], config.DET_IMG_SIZE)
            gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
            irimg = ir.to_ir_like(crop, ref_cdf)
            lines = [yolo_line(c, b) for c, b in plan["boxes"]]
            row["gray_path"], row["ir_path"], row["label_path"] = _write_pair(base, split, iid, gray, irimg, lines)
        rows.append(row)

    for m in progress(mrl_items, desc="write mrl"):
        g = f"mrl/s{m['subject']:04d}"
        split = mrl_split[g]
        iid = f"mrl__{m['path'].stem}"
        img = cv2.imread(str(m["path"]), cv2.IMREAD_GRAYSCALE)
        lines = [yolo_line(m["cls"], (0.5, 0.5, 1.0, 1.0))]
        gp, ip, lp = _write_pair(base, split, iid, img, img, lines)  # already IR: same pixels
        rows.append({"image_id": iid, "status": "ok", "reason": "", "kind": "closeup",
                     "source": "mrl_eye", "project": "mrl_eye", "licence": m["licence"],
                     "group_id": g, "split": split, "src_image": _rel(m["path"]),
                     "head_class": "", "n_eye_boxes": 1, "n_yawn_boxes": 0,
                     "raw_labels": "open" if m["cls"] == "open_eye" else "closed",
                     "gray_path": gp, "ir_path": ip, "label_path": lp})

    parts_dataset.write_csv(base / "index.csv", rows, INDEX_COLUMNS)
    for variant in ("gray", "ir"):
        (base / f"data_{variant}.yaml").write_text(
            f"# Argus parts detection dataset — {variant} variant. Paths are relative to this file.\n"
            f"train: {variant}/train/images\nval: {variant}/valid/images\ntest: {variant}/test/images\n"
            f"nc: {len(config.DET_CLASSES)}\nnames: {list(config.DET_CLASSES)}\n")
    return summarize(rows)


def summarize(rows: list[dict]) -> dict:
    ok = [r for r in rows if r["status"] == "ok"]
    boxes = defaultdict(Counter)
    base = paths.processed_dir() / "parts_det"
    for r in ok:
        for line in (base / r["label_path"]).read_text().splitlines():
            boxes[r["split"]][config.DET_CLASSES[int(line.split()[0])]] += 1
    return {
        "images_ok": len(ok),
        "images_per_split": dict(Counter(r["split"] for r in ok)),
        "images_per_kind": dict(Counter(f"{r['source']}/{r['kind']}" for r in ok)),
        "rejected": dict(Counter(r["reason"] for r in rows if r["status"] != "ok")),
        "boxes_per_split": {s: dict(c) for s, c in sorted(boxes.items())},
        "groups_crossing_splits": sum(
            1 for n in Counter(g for g, _ in {(r["group_id"], r["split"]) for r in ok}).values() if n > 1),
    }


_README = """Argus parts detection dataset (YOLO format)
==========================================

Images are square grayscale face crops (BlazeFace box + 25% margin, padded with black where it
leaves the frame, resized to {size}px), plus "closeup" images (MRL eye photos, Roboflow eye/mouth
close-ups) whose single box is the whole image.

Classes: {classes}
  head_straight / head_down are auto-labelled from FaceLandmarker pitch (|pitch| <= {straight} deg
  and |yaw| <= {yaw} deg -> straight; pitch >= {down} deg in the head-down direction -> down;
  otherwise no head box).

gray/  plain luminance grayscale.   ir/  IR-simulated (Pi NoIR camera): red-dominant mix +
histogram match toward MRL real-IR. MRL is real IR, so it is identical in both. Labels identical.
data_gray.yaml / data_ir.yaml: Ultralytics dataset files (paths relative to this folder).
index.csv: one row per SOURCE image, including rejected ones (status / reason), with group_id
and split. Splits are per group (original image + its augmented copies + near-duplicate frames,
or one MRL subject), so copies never straddle train/valid/test. Never re-split by image.

Sources and licences:
  MRL Eye: http://mrl.cs.vsb.cz/eyedataset.html (no licence stated). Cite: R. Fusek,
    "Pupil localization using geodesic distance", ISVC 2018. Capped at {mrl_cap} images per
    subject and eye state.
{roboflow}
"""


def export_zip(dest: Path | None = None) -> Path:
    base = paths.processed_dir() / "parts_det"
    dest = dest or paths.processed_dir() / "parts_det.zip"
    rf = "\n".join(
        f"  {s['project']} v{s['version']}: https://universe.roboflow.com/{s['workspace']}/{s['project']}"
        f" ({external.load_manifest()['sources'].get('roboflow/' + s['project'], {}).get('licence', '?')})"
        for s in config.ROBOFLOW_PROJECTS)
    readme = _README.format(
        size=config.DET_IMG_SIZE, classes=", ".join(config.DET_CLASSES),
        straight=config.HEAD_STRAIGHT_PITCH_DEG, yaw=config.HEAD_STRAIGHT_YAW_DEG,
        down=config.HEAD_DOWN_PITCH_DEG, mrl_cap=config.DET_MRL_MAX_PER_SUBJECT_CLASS, roboflow=rf)
    tmp = dest.with_suffix(".zip.tmp")
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_STORED) as zf:
        zf.writestr("README.txt", readme)
        for name in ("index.csv", "data_gray.yaml", "data_ir.yaml"):
            zf.write(base / name, name, compress_type=zipfile.ZIP_DEFLATED)
        for variant in ("gray", "ir"):
            for f in sorted((base / variant).rglob("*")):
                if f.is_file():
                    zf.write(f, f.relative_to(base).as_posix(),
                             compress_type=zipfile.ZIP_DEFLATED if f.suffix == ".txt" else zipfile.ZIP_STORED)
    tmp.replace(dest)
    return dest


def contact_sheet(n: int = 48, seed: int = 0, out: Path | None = None) -> Path:
    """Random accepted face crops with boxes + head class + pitch drawn on — used to check the
    pitch sign and the box mapping by eye."""
    import cv2
    import pandas as pd
    base = paths.processed_dir() / "parts_det"
    df = pd.read_csv(base / "index.csv", dtype=str, keep_default_na=False)
    df = df[(df.status == "ok") & (df.kind == "face")]
    colors = [(0, 255, 0), (0, 0, 255), (255, 0, 255), (255, 255, 0), (0, 165, 255)]
    tiles = []
    for _, r in df.sample(min(n, len(df)), random_state=seed).iterrows():
        im = cv2.cvtColor(cv2.imread(str(base / r["gray_path"]), cv2.IMREAD_GRAYSCALE), cv2.COLOR_GRAY2BGR)
        S = im.shape[0]
        for line in (base / r["label_path"]).read_text().splitlines():
            c, xc, yc, w, h = line.split()
            c = int(c)
            x0, y0 = int((float(xc) - float(w) / 2) * S), int((float(yc) - float(h) / 2) * S)
            x1, y1 = int((float(xc) + float(w) / 2) * S), int((float(yc) + float(h) / 2) * S)
            cv2.rectangle(im, (x0, y0), (x1, y1), colors[c], 1)
        txt = f"p{float(r['pitch']):+.0f} {r['head_class'] or '-'}" if r["pitch"] else "no pose"
        cv2.putText(im, txt, (3, S - 6), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 255, 255), 1)
        tiles.append(im)
    while len(tiles) % 8:
        tiles.append(np.zeros_like(tiles[0]))
    sheet = np.vstack([np.hstack(tiles[i:i + 8]) for i in range(0, len(tiles), 8)])
    out = out or base / "preview_faces.jpg"
    cv2.imwrite(str(out), sheet)
    return out
