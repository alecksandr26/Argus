"""Pull + catalogue the external datasets for the "parts" architecture (eye/mouth crops → tiny
CNNs). **Download-only**: nothing here crops, relabels or preprocesses — that's the later
part-crops stage. Every source lands under ``raw/`` and gets one entry in
``raw/datasets_manifest.json`` (counts, class list, licence, checksum, timestamp) so the class
lists can be reviewed before any label mapping is written.

Sources:
  * ``mrl_eye``         — MRL Eye zip (``config.MRL_EYE_URL``), unpacked to ``raw/mrl_eye/``.
  * ``roboflow/<proj>`` — each of ``config.ROBOFLOW_PROJECTS``, YOLO export via the ``roboflow``
                          SDK, into ``raw/roboflow/<project>/v<version>/``.
  * ``uta_rldd``        — not downloaded (already in ``raw/raw_videos/``); catalogued only.

Resumable: a source whose on-disk checksum still matches its manifest entry is skipped.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
import urllib.request
import zipfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from . import config, paths

IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".bmp")

# MRL filenames: s0001_00001_0_0_0_0_0_01.png
#   subject_imageid_gender_glasses_eyestate_reflections_lighting_sensor  (eyestate 0=closed 1=open)
_MRL_NAME = re.compile(
    r"^s(?P<subject>\d+)_(?P<image>\d+)_(?P<gender>\d)_(?P<glasses>\d)_(?P<eye_state>\d)_"
    r"(?P<reflections>\d)_(?P<lighting>\d)_(?P<sensor>\d+)$"
)


# --- manifest ------------------------------------------------------------------------------

def load_manifest(path: Path | None = None) -> dict:
    path = path or paths.datasets_manifest_json()
    if not path.exists():
        return {"sources": {}}
    return json.loads(path.read_text())


def save_manifest(manifest: dict, path: Path | None = None) -> None:
    """Atomic write (tmp + rename) so an interrupted run never leaves a half-written manifest."""
    path = path or paths.datasets_manifest_json()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    tmp.replace(path)


def upsert_source(name: str, entry: dict, path: Path | None = None) -> dict:
    manifest = load_manifest(path)
    entry = {**entry, "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    manifest["sources"][name] = entry
    save_manifest(manifest, path)
    return entry


# --- checksums -----------------------------------------------------------------------------

def sha256_file(path: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while block := f.read(chunk):
            h.update(block)
    return h.hexdigest()


def tree_fingerprint(root: Path) -> tuple[int, str]:
    """(file count, sha256 over every file's relative path + content hash). Used for sources
    that arrive already extracted (Roboflow), where there's no single archive to hash."""
    h = hashlib.sha256()
    files = sorted(p for p in root.rglob("*") if p.is_file())
    for p in files:
        h.update(p.relative_to(root).as_posix().encode())
        h.update(b"\0")
        h.update(sha256_file(p).encode())
    return len(files), h.hexdigest()


def _images_under(root: Path) -> list[Path]:
    return sorted(p for p in root.rglob("*") if p.suffix.lower() in IMAGE_EXTS and p.is_file())


# --- MRL Eye -------------------------------------------------------------------------------

def parse_mrl_filename(name: str) -> dict | None:
    m = _MRL_NAME.match(Path(name).stem)
    if not m:
        return None
    d = {k: int(v) for k, v in m.groupdict().items()}
    d["eye_state"] = "open" if d["eye_state"] == 1 else "closed"
    return d


def summarize_mrl(root: Path) -> dict:
    images = _images_under(root)
    parsed = [parse_mrl_filename(p.name) for p in images]
    good = [d for d in parsed if d]
    return {
        "images": len(images),
        "unparsed_filenames": len(images) - len(good),
        "subjects": len({d["subject"] for d in good}),
        "classes": dict(Counter(d["eye_state"] for d in good)),
        "sensors": dict(Counter(str(d["sensor"]) for d in good)),
        "glasses": sum(d["glasses"] for d in good),
    }


def _urlretrieve(url: str, dest: Path) -> None:
    tmp = dest.with_suffix(dest.suffix + ".part")
    print(f"Downloading {url}\n        -> {dest}")
    urllib.request.urlretrieve(url, tmp)
    tmp.replace(dest)


def ensure_mrl_eye(download: Callable[[str, Path], None] = _urlretrieve,
                   force: bool = False) -> dict:
    root = paths.mrl_eye_raw_dir()
    root.mkdir(parents=True, exist_ok=True)
    zip_path = root / config.MRL_EYE_ZIP_NAME
    extract_dir = root / Path(config.MRL_EYE_ZIP_NAME).stem
    prev = load_manifest()["sources"].get("mrl_eye", {})

    if force or not (zip_path.exists() and zip_path.stat().st_size > 0):
        download(config.MRL_EYE_URL, zip_path)
    sha = sha256_file(zip_path)

    if (not force and prev.get("archive_sha256") == sha and extract_dir.is_dir()
            and prev.get("images") == len(_images_under(extract_dir))):
        print(f"SKIP mrl_eye — archive + extracted tree match the manifest ({prev['images']} images)")
        return prev

    if extract_dir.exists():
        shutil.rmtree(extract_dir)
    with zipfile.ZipFile(zip_path) as zf:
        zf.extractall(root)  # the zip's top-level folder is mrlEyes_2018_01/
    if not extract_dir.is_dir():  # tolerate an archive without the top-level folder
        raise RuntimeError(f"expected {extract_dir} after extracting {zip_path}")

    summary = summarize_mrl(extract_dir)
    ok = (summary["images"] == config.MRL_EYE_EXPECTED_IMAGES
          and summary["subjects"] == config.MRL_EYE_EXPECTED_SUBJECTS)
    return upsert_source("mrl_eye", {
        "kind": "mrl_eye",
        "url": config.MRL_EYE_URL,
        "licence": config.MRL_EYE_LICENCE,
        "location": str(extract_dir.relative_to(paths.dataset_root())),
        "archive_sha256": sha,
        "expected_images": config.MRL_EYE_EXPECTED_IMAGES,
        "expected_subjects": config.MRL_EYE_EXPECTED_SUBJECTS,
        "status": "ok" if ok else "count_mismatch",
        **summary,
    })


# --- Roboflow ------------------------------------------------------------------------------

def parse_data_yaml(path: Path) -> list[str]:
    """Class names from a YOLO ``data.yaml``. Uses PyYAML (a ``roboflow`` dependency) when
    present, else a minimal parser for the two forms Roboflow writes (inline list / block list)."""
    text = path.read_text()
    try:
        import yaml
        names = yaml.safe_load(text).get("names", [])
        return list(names.values()) if isinstance(names, dict) else [str(n) for n in names]
    except ImportError:
        pass
    m = re.search(r"^names:\s*\[(.*?)\]", text, re.M | re.S)
    if m:
        return [s.strip().strip("'\"") for s in m.group(1).split(",") if s.strip()]
    out, in_block = [], False
    for line in text.splitlines():
        if line.startswith("names:"):
            in_block = True
            continue
        if in_block:
            if line.lstrip().startswith("- "):
                out.append(line.split("- ", 1)[1].strip().strip("'\""))
            elif line and not line[0].isspace():
                break
    return out


def parse_roboflow_licence(export_dir: Path) -> str:
    """Roboflow exports carry ``README.roboflow.txt`` with a ``License: …`` line."""
    for name in ("README.roboflow.txt", "README.dataset.txt"):
        p = export_dir / name
        if p.exists():
            m = re.search(r"^\s*Licen[sc]e:\s*(.+)$", p.read_text(errors="replace"), re.M | re.I)
            if m:
                return m.group(1).strip()
    return "unverified"


def summarize_yolo_export(export_dir: Path) -> dict:
    """Counts per split + per class (from label files), class list, licence."""
    names_file = next(iter(sorted(export_dir.glob("data.y*ml"))), None)
    names = parse_data_yaml(names_file) if names_file else []
    splits, boxes = {}, Counter()
    for split_dir in sorted(p for p in export_dir.iterdir() if p.is_dir()):
        imgs = _images_under(split_dir / "images") if (split_dir / "images").is_dir() else []
        if not imgs:
            continue
        splits[split_dir.name] = len(imgs)
        for lbl in (split_dir / "labels").glob("*.txt") if (split_dir / "labels").is_dir() else []:
            for line in lbl.read_text().splitlines():
                parts = line.split()
                if parts:
                    cid = int(float(parts[0]))
                    boxes[names[cid] if cid < len(names) else f"id_{cid}"] += 1
    return {
        "images": sum(splits.values()),
        "splits": splits,
        "class_names": names,
        "boxes_per_class": dict(boxes),
        "licence": parse_roboflow_licence(export_dir),
    }


def _source_key(spec: dict) -> str:
    return f"roboflow/{spec['project']}"


def _resolve_version(project, spec: dict) -> int:
    if spec.get("version") is not None:
        return int(spec["version"])
    versions = project.versions()
    if not versions:
        raise RuntimeError(f"{spec['workspace']}/{spec['project']} has no published versions")
    return max(int(str(v.version).split("/")[-1]) for v in versions)


def ensure_roboflow_project(spec: dict, rf, force: bool = False) -> dict:
    """``rf`` is a ``roboflow.Roboflow`` client (injected so tests can stub it)."""
    key = _source_key(spec)
    project = rf.workspace(spec["workspace"]).project(spec["project"])
    version = _resolve_version(project, spec)
    dest = paths.roboflow_raw_dir() / spec["project"] / f"v{version}"
    prev = load_manifest()["sources"].get(key, {})

    if not force and dest.is_dir() and prev.get("version") == version:
        n, fp = tree_fingerprint(dest)
        if prev.get("tree_sha256") == fp:
            print(f"SKIP {key} v{version} — on-disk tree matches the manifest ({prev['images']} images)")
            return prev

    # Download into a sibling temp dir, then rename — an interrupted download never looks done.
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix=f".v{version}.", dir=dest.parent))
    try:
        print(f"Downloading {spec['workspace']}/{spec['project']} v{version} "
              f"({config.ROBOFLOW_EXPORT_FORMAT}) -> {dest}")
        project.version(version).download(config.ROBOFLOW_EXPORT_FORMAT, location=str(tmp),
                                          overwrite=True)
        if dest.exists():
            shutil.rmtree(dest)
        tmp.replace(dest)
    finally:
        if tmp.exists():
            shutil.rmtree(tmp, ignore_errors=True)

    n_files, fp = tree_fingerprint(dest)
    summary = summarize_yolo_export(dest)
    return upsert_source(key, {
        "kind": "roboflow",
        "workspace": spec["workspace"],
        "project": spec["project"],
        "version": version,
        "version_pinned": spec.get("version") is not None,
        "url": f"https://universe.roboflow.com/{spec['workspace']}/{spec['project']}",
        "export_format": config.ROBOFLOW_EXPORT_FORMAT,
        "location": str(dest.relative_to(paths.dataset_root())),
        "listed_images": spec.get("listed_images"),
        "note": spec.get("note", ""),
        "files": n_files,
        "tree_sha256": fp,
        "status": "ok" if summary["images"] else "empty_export",
        **summary,
    })


def roboflow_client(api_key: str | None = None):
    api_key = api_key or os.environ.get("ROBOFLOW_API_KEY")
    if not api_key:
        raise SystemExit(
            "ROBOFLOW_API_KEY is not set. Get a free key at https://app.roboflow.com/settings/api "
            "and run:  ROBOFLOW_API_KEY=... python scripts/fetch_roboflow_parts.py"
        )
    try:
        from roboflow import Roboflow
    except ImportError as e:
        raise SystemExit("the `roboflow` package is missing: pip install -r requirements.txt") from e
    return Roboflow(api_key=api_key)


# --- UTA-RLDD (catalogue only) -------------------------------------------------------------

def catalogue_uta_rldd() -> dict:
    root = paths.raw_dir()
    clips = sorted(root.glob("subject_*/level_*_clip_*.mp4")) if root.is_dir() else []
    levels = Counter(int(re.match(r"level_(\d)", c.name).group(1)) for c in clips)
    return upsert_source("uta_rldd", {
        "kind": "uta_rldd",
        "url": "https://sites.google.com/view/utarldd/home",
        "licence": "unverified (see the UTA-RLDD site)",
        "location": str(root.relative_to(paths.dataset_root())),
        "subjects": len({c.parent.name for c in clips}),
        "clips": len(clips),
        "clips_per_level": {str(k): v for k, v in sorted(levels.items())},
        "status": "ok" if clips else "missing",
    })


# --- status --------------------------------------------------------------------------------

def check_source_on_disk(name: str, entry: dict) -> str:
    """Cheap re-validation for ``datasets_status.py``: does disk still match the manifest?"""
    loc = paths.dataset_root() / entry.get("location", "")
    if not loc.exists():
        return "MISSING on disk"
    if entry.get("kind") == "mrl_eye":
        n = len(_images_under(loc))
        return "ok" if n == entry.get("images") else f"image count changed ({n})"
    if entry.get("kind") == "roboflow":
        _, fp = tree_fingerprint(loc)
        return "ok" if fp == entry.get("tree_sha256") else "tree changed since download"
    return "ok"
