"""external.py — dataset pull + manifest (parts architecture). Network is always stubbed."""

import importlib
import zipfile

import pytest


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("ARGUS_DATASET_ROOT", str(tmp_path))
    from argus_dataset import paths
    importlib.reload(paths)
    from argus_dataset import external
    importlib.reload(external)
    paths.ensure_dirs()
    return paths, external


# --- manifest ------------------------------------------------------------------------------

def test_manifest_roundtrip_and_upsert(env):
    paths, ext = env
    assert ext.load_manifest() == {"sources": {}}
    ext.upsert_source("a", {"images": 3})
    ext.upsert_source("b", {"images": 5})
    ext.upsert_source("a", {"images": 4})
    m = ext.load_manifest()
    assert set(m["sources"]) == {"a", "b"}
    assert m["sources"]["a"]["images"] == 4 and "updated_at" in m["sources"]["a"]
    assert not paths.datasets_manifest_json().with_suffix(".json.tmp").exists()


def test_tree_fingerprint_changes_with_content(env, tmp_path):
    _, ext = env
    d = tmp_path / "t"
    (d / "x").mkdir(parents=True)
    (d / "x" / "a.txt").write_text("1")
    n1, f1 = ext.tree_fingerprint(d)
    assert (n1, f1) == ext.tree_fingerprint(d)
    (d / "x" / "a.txt").write_text("2")
    n2, f2 = ext.tree_fingerprint(d)
    assert n1 == n2 == 1 and f1 != f2


# --- parsers -------------------------------------------------------------------------------

def test_parse_mrl_filename(env):
    _, ext = env
    d = ext.parse_mrl_filename("s0012_00345_1_1_0_2_1_03.png")
    assert d["subject"] == 12 and d["glasses"] == 1 and d["eye_state"] == "closed"
    assert d["reflections"] == 2 and d["sensor"] == 3
    assert ext.parse_mrl_filename("s0012_00345_1_1_1_0_1_01.png")["eye_state"] == "open"
    assert ext.parse_mrl_filename("README.txt") is None


@pytest.mark.parametrize("text", [
    "nc: 3\nnames: ['Closed-Eye', 'Open-Eye', 'Yawn']\n",
    "nc: 3\nnames:\n- Closed-Eye\n- Open-Eye\n- Yawn\nroboflow:\n  workspace: x\n",
])
def test_parse_data_yaml_both_forms(env, tmp_path, text):
    _, ext = env
    p = tmp_path / "data.yaml"
    p.write_text(text)
    assert ext.parse_data_yaml(p) == ["Closed-Eye", "Open-Eye", "Yawn"]


# --- fake sources --------------------------------------------------------------------------

def _write_yolo_export(root, names=("closed", "open", "yawn")):
    root.mkdir(parents=True, exist_ok=True)
    (root / "data.yaml").write_text(f"names: {list(names)}\n")
    (root / "README.roboflow.txt").write_text("Project\nLicense: CC BY 4.0\n")
    for split, n in (("train", 3), ("valid", 1)):
        (root / split / "images").mkdir(parents=True)
        (root / split / "labels").mkdir(parents=True)
        for i in range(n):
            (root / split / "images" / f"{i}.jpg").write_bytes(b"jpg" + bytes([i]))
            (root / split / "labels" / f"{i}.txt").write_text("1 0.5 0.5 0.1 0.1\n2 0.5 0.8 0.2 0.1\n")


class _FakeVersion:
    def __init__(self, n, calls):
        self.version, self._calls = str(n), calls

    def download(self, fmt, location, overwrite):
        self._calls.append((self.version, fmt))
        from pathlib import Path
        _write_yolo_export(Path(location))


class _FakeProject:
    def __init__(self, calls):
        self.calls = calls

    def versions(self):
        return [_FakeVersion(1, self.calls), _FakeVersion(3, self.calls)]

    def version(self, n):
        return _FakeVersion(n, self.calls)


class _FakeRF:
    def __init__(self):
        self.calls = []

    def workspace(self, ws):
        return self

    def project(self, p):
        return _FakeProject(self.calls)


SPEC = {"workspace": "ws", "project": "proj", "version": None, "listed_images": 4, "note": "n"}


def test_roboflow_download_summary_and_skip(env):
    paths, ext = env
    rf = _FakeRF()
    e = ext.ensure_roboflow_project(SPEC, rf)
    assert rf.calls == [("3", "yolov8")]                    # latest version resolved
    assert e["version"] == 3 and not e["version_pinned"]
    assert e["images"] == 4 and e["splits"] == {"train": 3, "valid": 1}
    assert e["class_names"] == ["closed", "open", "yawn"]
    assert e["boxes_per_class"] == {"open": 4, "yawn": 4}
    assert e["licence"] == "CC BY 4.0" and e["status"] == "ok"
    assert (paths.roboflow_raw_dir() / "proj" / "v3" / "data.yaml").exists()
    assert not list((paths.roboflow_raw_dir() / "proj").glob(".v3.*"))  # temp dir cleaned up

    ext.ensure_roboflow_project(SPEC, rf)                    # checksum matches -> skipped
    assert len(rf.calls) == 1
    assert ext.check_source_on_disk("roboflow/proj", ext.load_manifest()["sources"]["roboflow/proj"]) == "ok"

    (paths.roboflow_raw_dir() / "proj" / "v3" / "train" / "images" / "0.jpg").write_bytes(b"x")
    ext.ensure_roboflow_project(SPEC, rf)                    # tree changed -> re-downloaded
    assert len(rf.calls) == 2


def test_roboflow_pinned_version(env):
    _, ext = env
    rf = _FakeRF()
    e = ext.ensure_roboflow_project({**SPEC, "version": 1}, rf)
    assert rf.calls == [("1", "yolov8")] and e["version_pinned"]


def test_mrl_download_extract_and_skip(env, tmp_path, monkeypatch):
    paths, ext = env
    from argus_dataset import config
    monkeypatch.setattr(config, "MRL_EYE_EXPECTED_IMAGES", 3)
    monkeypatch.setattr(config, "MRL_EYE_EXPECTED_SUBJECTS", 2)
    names = ["s0001_00001_0_0_0_0_0_01.png", "s0001_00002_0_0_1_0_1_01.png",
             "s0002_00001_1_1_1_0_1_02.png"]
    downloads = []

    def fake_download(url, dest):
        downloads.append(url)
        with zipfile.ZipFile(dest, "w") as zf:
            for n in names:
                zf.writestr(f"mrlEyes_2018_01/s{n[1:5]}/{n}", b"png")

    e = ext.ensure_mrl_eye(download=fake_download)
    assert e["status"] == "ok" and e["images"] == 3 and e["subjects"] == 2
    assert e["classes"] == {"closed": 1, "open": 2} and e["glasses"] == 1
    assert len(downloads) == 1

    again = ext.ensure_mrl_eye(download=fake_download)       # zip present + counts match -> skip
    assert len(downloads) == 1 and again["archive_sha256"] == e["archive_sha256"]


def test_mrl_count_mismatch_flagged(env, monkeypatch):
    _, ext = env

    def fake_download(url, dest):
        with zipfile.ZipFile(dest, "w") as zf:
            zf.writestr("mrlEyes_2018_01/s0001/s0001_00001_0_0_0_0_0_01.png", b"png")

    assert ext.ensure_mrl_eye(download=fake_download)["status"] == "count_mismatch"


def test_catalogue_uta_rldd(env):
    paths, ext = env
    for subj, name in (("subject_07", "level_1_clip_01.mp4"), ("subject_07", "level_2_clip_01.mp4"),
                       ("subject_08", "level_1_clip_01.mp4")):
        (paths.raw_dir() / subj).mkdir(parents=True, exist_ok=True)
        (paths.raw_dir() / subj / name).write_bytes(b"")
    e = ext.catalogue_uta_rldd()
    assert e["subjects"] == 2 and e["clips"] == 3 and e["clips_per_level"] == {"1": 2, "2": 1}


def test_roboflow_client_requires_key(env, monkeypatch):
    _, ext = env
    monkeypatch.delenv("ROBOFLOW_API_KEY", raising=False)
    with pytest.raises(SystemExit, match="ROBOFLOW_API_KEY"):
        ext.roboflow_client()
