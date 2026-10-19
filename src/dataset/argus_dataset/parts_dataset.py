"""Merge MRL Eye + the Roboflow projects into one 3-class parts dataset
(``open_eye`` / ``closed_eye`` / ``yawn``), with an IR-simulated copy of every RGB crop.

Output under ``processed/parts_dataset/``:

    rgb/<class>/<crop_id>.jpg     box crop as-is (Roboflow only; MRL images are referenced in place)
    ir/<class>/<crop_id>.png      IR-like grayscale (ir.to_ir_like); MRL → its own raw path
    parts_dataset.csv             ONE row per crop, all sources (the single labels file)
    class_map.csv                 every (project, raw class) → merged class or DROP, with counts
    ir_ref_hist.npy               MRL reference CDF used by the IR filter

**Leakage.** Roboflow version exports contain augmented copies named
``<original>_jpg.rf.<hash>.jpg``, and forked projects re-upload the same originals. Rows are
grouped by (project, original stem), and groups whose source images are byte-identical after
downscaling (equal 64-bit dHash) are unioned across projects. MRL is grouped by subject. The
``split`` column is assigned **per group** from a stable hash, so copies never straddle
train/valid/test.

Rebuilt wholesale on each run (a few minutes on ~19k images); existing crop files are reused.
"""

from __future__ import annotations

import csv
import hashlib
import random
import re
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np

from . import config, external, ir, paths

CSV_COLUMNS = [
    "crop_id", "class", "part", "source", "project", "version", "raw_class", "licence",
    "group_id", "split", "source_split", "src_image", "box_xc", "box_yc", "box_w", "box_h",
    "crop_w", "crop_h", "rgb_path", "ir_path",
]
_RF_SUFFIX = re.compile(r"(_(jpg|jpeg|png))?\.rf\.[0-9a-f]+$", re.I)


def original_stem(filename: str) -> str:
    """``106_jpg.rf.82d9…`` → ``106``: the pre-augmentation original an export image came from."""
    return _RF_SUFFIX.sub("", Path(filename).stem)


def dhash(gray: np.ndarray, size: int = config.PARTS_DHASH_SIZE) -> int:
    import cv2
    small = cv2.resize(gray, (size + 1, size), interpolation=cv2.INTER_AREA)
    bits = (small[:, 1:] > small[:, :-1]).ravel()
    return int("".join("1" if b else "0" for b in bits), 2)


def assign_splits(group_ids, fracs=config.PARTS_SPLIT_FRACS) -> dict[str, str]:
    """Stable group → split. Groups are ordered by a hash of their id and cut at the cumulative
    fractions **by rank**, not by hash value: with few groups (MRL has 37 subjects) thresholding
    the hash itself can leave a split empty."""
    order = sorted(set(group_ids), key=lambda g: hashlib.sha1(g.encode()).hexdigest())
    n = len(order)
    n_train = round(n * fracs[0])
    n_valid = round(n * (fracs[0] + fracs[1])) - n_train
    if n >= 3:  # keep every split non-empty whenever there are enough groups
        n_valid = max(1, n_valid)
        n_train = min(n_train, n - n_valid - 1)
    return {g: ("train" if i < n_train else "valid" if i < n_train + n_valid else "test")
            for i, g in enumerate(order)}


def square_crop_box(xc, yc, w, h, W, H, pad=config.PARTS_CROP_PAD_FRAC):
    """Normalised YOLO box → padded, squared pixel box clipped to the image."""
    side = max(w * W, h * H) * (1 + 2 * pad)
    cx, cy = xc * W, yc * H
    x0, y0 = int(round(cx - side / 2)), int(round(cy - side / 2))
    x1, y1 = int(round(cx + side / 2)), int(round(cy + side / 2))
    return max(0, x0), max(0, y0), min(W, x1), min(H, y1)


class _UnionFind:
    def __init__(self):
        self.parent = {}

    def find(self, x):
        self.parent.setdefault(x, x)
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[max(ra, rb)] = min(ra, rb)


# --- IR reference ---------------------------------------------------------------------------

def build_ir_reference(mrl_root: Path, n: int = config.IR_REF_SAMPLE_IMAGES, seed: int = 0) -> np.ndarray:
    import cv2
    imgs = sorted(p for p in mrl_root.rglob("*.png"))
    if not imgs:
        raise SystemExit(f"no MRL images under {mrl_root} — run scripts/fetch_mrl_eye.py first")
    sample = random.Random(seed).sample(imgs, min(n, len(imgs)))
    cdf = ir.reference_cdf(cv2.imread(str(p), cv2.IMREAD_GRAYSCALE) for p in sample)
    out = paths.ir_reference_hist()
    out.parent.mkdir(parents=True, exist_ok=True)
    np.save(out, cdf)
    return cdf


# --- sources --------------------------------------------------------------------------------

def _rel(p: Path) -> str:
    return p.relative_to(paths.dataset_root()).as_posix()


def mrl_rows(manifest: dict) -> list[dict]:
    e = manifest["sources"].get("mrl_eye")
    if not e:
        return []
    root = paths.dataset_root() / e["location"]
    rows = []
    for p in sorted(root.rglob("*.png")):
        d = external.parse_mrl_filename(p.name)
        if not d:
            continue
        cls = "open_eye" if d["eye_state"] == "open" else "closed_eye"
        group = f"mrl/s{d['subject']:04d}"
        rows.append({
            "crop_id": f"mrl_{p.stem}", "class": cls, "part": "eye", "source": "mrl_eye",
            "project": "mrl_eye", "version": "", "raw_class": d["eye_state"],
            "licence": e.get("licence", ""), "group_id": group, "split": "",
            "source_split": "", "src_image": _rel(p), "box_xc": 0.5, "box_yc": 0.5, "box_w": 1.0,
            "box_h": 1.0, "crop_w": "", "crop_h": "", "rgb_path": "", "ir_path": _rel(p),
        })
    return rows


def _read_yolo_labels(lbl: Path) -> tuple[list[tuple[int, float, float, float, float]], int]:
    boxes, polys = [], 0
    for line in lbl.read_text().splitlines():
        parts = line.split()
        if len(parts) == 5:
            c, *xywh = parts
            boxes.append((int(float(c)), *map(float, xywh)))
        elif len(parts) == 9:
            # A 4-corner polygon: some exports (headpose-w4zuc) write every box this way. Taken
            # as its bounding box. Longer polygons (real segmentations) are still skipped.
            c, *xy = parts
            xs, ys = [float(v) for v in xy[0::2]], [float(v) for v in xy[1::2]]
            boxes.append((int(float(c)), (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2,
                          max(xs) - min(xs), max(ys) - min(ys)))
        elif len(parts) > 5:
            polys += 1      # segmentation polygon — not a box, skipped
    return boxes, polys


def roboflow_rows(manifest: dict, ref_cdf: np.ndarray | None, stats: Counter,
                  map_counts: Counter, progress=lambda it, **k: it) -> list[dict]:
    import cv2
    out_root = paths.parts_dataset_dir()
    rows, uf, hash_owner = [], _UnionFind(), {}
    # Head-pose-only projects have no eye/mouth boxes to crop; they feed detection_dataset only.
    sources = [(k, e) for k, e in sorted(manifest["sources"].items()) if e.get("kind") == "roboflow"
               and e["project"] not in config.ROBOFLOW_HEADPOSE_PROJECTS]
    for key, e in sources:
        project, cmap = e["project"], config.ROBOFLOW_CLASS_MAP.get(e["project"])
        if cmap is None:
            raise SystemExit(f"no ROBOFLOW_CLASS_MAP entry for {project} — add one in config.py")
        names = e["class_names"]
        unknown = set(names) - set(cmap)
        if unknown:
            raise SystemExit(f"{project}: classes {sorted(unknown)} missing from ROBOFLOW_CLASS_MAP")
        root = paths.dataset_root() / e["location"]
        images = sorted(p for p in root.glob("*/images/*") if p.suffix.lower() in external.IMAGE_EXTS)
        for img_path in progress(images, desc=project):
            lbl = img_path.parent.parent / "labels" / (img_path.stem + ".txt")
            if not lbl.exists():
                continue
            boxes, polys = _read_yolo_labels(lbl)
            stats["polygons_skipped"] += polys
            kept = [(c, *b) for c, *b in boxes if c < len(names) and cmap.get(names[c])]
            for c, *_ in boxes:
                map_counts[(project, names[c] if c < len(names) else f"id_{c}")] += 1
            if not kept:
                continue
            img = cv2.imread(str(img_path))
            if img is None:
                stats["unreadable_images"] += 1
                continue
            H, W = img.shape[:2]
            group = f"rf/{project}/{original_stem(img_path.name)}"
            h = dhash(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY))
            if h in hash_owner:
                uf.union(group, hash_owner[h])
            else:
                hash_owner[h] = group
            uf.find(group)
            for i, (c, xc, yc, w, hh) in enumerate(kept):
                cls = cmap[names[c]]
                x0, y0, x1, y1 = square_crop_box(xc, yc, w, hh, W, H)
                if min(x1 - x0, y1 - y0) < config.PARTS_MIN_CROP_PX:
                    stats["too_small"] += 1
                    continue
                crop_id = f"{project}_{img_path.stem}_{i}"
                rgb_p = out_root / "rgb" / cls / f"{crop_id}.jpg"
                ir_p = out_root / "ir" / cls / f"{crop_id}.png"
                if not (rgb_p.exists() and ir_p.exists()):
                    crop = img[y0:y1, x0:x1]
                    rgb_p.parent.mkdir(parents=True, exist_ok=True)
                    ir_p.parent.mkdir(parents=True, exist_ok=True)
                    cv2.imwrite(str(rgb_p), crop, [cv2.IMWRITE_JPEG_QUALITY, 95])
                    cv2.imwrite(str(ir_p), ir.to_ir_like(crop, ref_cdf))
                rows.append({
                    "crop_id": crop_id, "class": cls, "part": config.PARTS_CLASS_PART[cls],
                    "source": "roboflow", "project": project, "version": e["version"],
                    "raw_class": names[c], "licence": e.get("licence", ""), "group_id": group,
                    "split": "", "source_split": img_path.parent.parent.name,
                    "src_image": _rel(img_path), "box_xc": round(xc, 6), "box_yc": round(yc, 6),
                    "box_w": round(w, 6), "box_h": round(hh, 6), "crop_w": x1 - x0,
                    "crop_h": y1 - y0, "rgb_path": _rel(rgb_p), "ir_path": _rel(ir_p),
                })
    for r in rows:  # resolve cross-project duplicate groups (split is assigned in build())
        r["group_id"] = uf.find(r["group_id"])
    return rows


# --- zip export -----------------------------------------------------------------------------

_ZIP_README = """Argus parts dataset — open_eye / closed_eye / yawn
================================================

parts_dataset.csv   one row per crop, every source. Paths are relative to this zip's root.
                    split (train/valid/test) is assigned per group_id: an original image plus
                    its augmented copies and near-duplicate frames, or one MRL subject. Never
                    re-split by row, or copies leak across splits.
class_map.csv       how every source label was merged into the 3 classes (or dropped).
rgb/<class>/        original colour crops (Roboflow sources only).
ir/<class>/         IR-like grayscale crops (red-dominant mix + histogram match toward MRL).
                    MRL rows point here too: MRL is real infrared, so it was not simulated.
ir_ref_hist.npy     the MRL reference CDF used by the IR filter.

Sources and licences:
  MRL Eye: http://mrl.cs.vsb.cz/eyedataset.html (no licence stated). Cite: R. Fusek,
    "Pupil localization using geodesic distance", ISVC 2018.
{roboflow}
"""


def export_zip(dest: Path | None = None) -> Path:
    """Package the built dataset as one self-contained zip. Images are stored (already
    compressed) and the CSV is deflated. Paths in the bundled CSV are zip-relative, and MRL
    images are copied into ``ir/<class>/``."""
    import zipfile

    import pandas as pd
    dest = dest or paths.processed_dir() / "parts_dataset.zip"
    df = pd.read_csv(paths.parts_dataset_csv(), dtype=str, keep_default_na=False)
    root, base = paths.dataset_root(), paths.parts_dataset_dir()

    def zip_rel(p: str, row) -> str:
        if not p:
            return ""
        if row["source"] == "mrl_eye":
            return f"ir/{row['class']}/{row['crop_id']}.png"
        return (root / p).relative_to(base).as_posix()

    files = []
    for col in ("rgb_path", "ir_path"):
        new = [zip_rel(p, r) for p, r in zip(df[col], df.to_dict("records"))]
        files += [(root / p, z) for p, z in zip(df[col], new) if p]
        df[col] = new

    licences = "\n".join(sorted(
        f"  {p} ({lic}): https://universe.roboflow.com/"
        f"{next(s['workspace'] for s in config.ROBOFLOW_PROJECTS if s['project'] == p)}/{p}"
        for p, lic in df[df.source == "roboflow"].groupby("project")["licence"].first().items()))
    tmp = dest.with_suffix(".zip.tmp")
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_STORED) as zf:
        zf.writestr("parts_dataset.csv", df.to_csv(index=False), compress_type=zipfile.ZIP_DEFLATED)
        zf.write(paths.parts_class_map_csv(), "class_map.csv", compress_type=zipfile.ZIP_DEFLATED)
        if paths.ir_reference_hist().exists():
            zf.write(paths.ir_reference_hist(), "ir_ref_hist.npy")
        zf.writestr("README.txt", _ZIP_README.replace("{roboflow}", licences))
        for src, arc in sorted(set(files), key=lambda t: t[1]):
            zf.write(src, arc)
    tmp.replace(dest)
    return dest


# --- driver ---------------------------------------------------------------------------------

def write_csv(path: Path, rows: list[dict], columns: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".csv.tmp")
    with open(tmp, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=columns)
        w.writeheader()
        w.writerows(rows)
    tmp.replace(path)


def build(progress=lambda it, **k: it) -> dict:
    manifest = external.load_manifest()
    mrl = manifest["sources"].get("mrl_eye")
    ref_cdf = build_ir_reference(paths.dataset_root() / mrl["location"]) if mrl else None
    stats, map_counts = Counter(), Counter()
    rows = mrl_rows(manifest) + roboflow_rows(manifest, ref_cdf, stats, map_counts, progress)
    for source in {r["source"] for r in rows}:  # split per source so MRL subjects reach every split
        split_of = assign_splits(r["group_id"] for r in rows if r["source"] == source)
        for r in rows:
            if r["source"] == source:
                r["split"] = split_of[r["group_id"]]
    write_csv(paths.parts_dataset_csv(), rows, CSV_COLUMNS)

    cmap_rows = [
        {"project": p, "raw_class": raw, "class": config.ROBOFLOW_CLASS_MAP[p].get(raw) or "DROP",
         "boxes": n}
        for (p, raw), n in sorted(map_counts.items())
    ]
    if mrl:
        cmap_rows += [{"project": "mrl_eye", "raw_class": k, "class": f"{k}_eye", "boxes": v}
                      for k, v in sorted(mrl["classes"].items())]
    write_csv(paths.parts_class_map_csv(), cmap_rows, ["project", "raw_class", "class", "boxes"])

    by = defaultdict(Counter)
    for r in rows:
        by[r["split"]][r["class"]] += 1
    group_sizes = Counter(r["group_id"] for r in rows if r["source"] == "roboflow")
    return {
        "largest_roboflow_group": max(group_sizes.values(), default=0),
        "rows": len(rows),
        "per_class": dict(Counter(r["class"] for r in rows)),
        "per_source_class": {f"{s}/{c}": n for (s, c), n in
                             sorted(Counter((r["project"], r["class"]) for r in rows).items())},
        "per_split": {s: dict(c) for s, c in sorted(by.items())},
        "groups": len({r["group_id"] for r in rows}),
        **dict(stats),
    }
