"""ir.py + parts_dataset.py — IR simulation, class merge, leakage-safe grouping/splits, zip."""

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
    from argus_dataset import external, parts_dataset
    importlib.reload(external)
    importlib.reload(parts_dataset)
    paths.ensure_dirs()
    return paths, external, parts_dataset


# --- IR filter -----------------------------------------------------------------------------

def test_red_mix_weights_red_channel():
    from argus_dataset import ir
    red = np.zeros((4, 4, 3), np.uint8); red[..., 2] = 200
    blue = np.zeros((4, 4, 3), np.uint8); blue[..., 0] = 200
    assert ir.red_mix(red).mean() > 150 > 20 > ir.red_mix(blue).mean()
    assert ir.red_mix(red).shape == (4, 4) and ir.red_mix(red).dtype == np.uint8


def test_match_to_cdf_moves_toward_reference():
    from argus_dataset import ir
    rng = np.random.default_rng(0)
    ref = ir.reference_cdf([rng.integers(150, 256, (32, 32), dtype=np.uint8)])  # bright reference
    dark = rng.integers(0, 80, (32, 32), dtype=np.uint8)
    full, half = ir.match_to_cdf(dark, ref, 1.0), ir.match_to_cdf(dark, ref, 0.5)
    assert full.mean() > half.mean() > dark.mean()
    assert full.min() >= 140 and full.dtype == np.uint8
    np.testing.assert_array_equal(ir.match_to_cdf(dark, ref, 0.0), dark)


# --- helpers -------------------------------------------------------------------------------

@pytest.mark.parametrize("name,stem", [
    ("106_jpg.rf.82d96229c31442764f72440a638314c7.jpg", "106"),
    ("001_glasses_sleepyCombination_1001_drowsy_jpg.rf.2097990b.jpg",
     "001_glasses_sleepyCombination_1001_drowsy"),
    ("plain.png", "plain"),
])
def test_original_stem(env, name, stem):
    assert env[2].original_stem(name) == stem


def test_assign_splits_never_empties_a_split(env):
    pd_ = env[2]
    for n in (3, 5, 37, 500):
        s = pd_.assign_splits([f"g{i}" for i in range(n)])
        assert set(s.values()) == {"train", "valid", "test"}
        assert s == pd_.assign_splits(reversed([f"g{i}" for i in range(n)]))  # order-independent


def test_square_crop_box_is_square_and_clipped(env):
    x0, y0, x1, y1 = env[2].square_crop_box(0.5, 0.5, 0.2, 0.1, 100, 100, pad=0.0)
    assert (x1 - x0, y1 - y0) == (20, 20)
    x0, y0, x1, y1 = env[2].square_crop_box(0.02, 0.02, 0.2, 0.2, 100, 100)
    assert x0 == 0 and y0 == 0


# --- end-to-end on a fake Roboflow export + fake MRL ---------------------------------------

def _img(seed, size=64):
    return np.random.default_rng(seed).integers(0, 256, (size, size, 3), dtype=np.uint8)


def _fake_sources(paths, external, monkeypatch):
    from argus_dataset import config
    proj = "drowsiness-hgjay"
    root = paths.roboflow_raw_dir() / proj / "v1"
    for split in ("train", "valid"):
        (root / split / "images").mkdir(parents=True)
        (root / split / "labels").mkdir(parents=True)
    # class ids: 0=close_eyes 1=open_eyes 2=face 3=yawn.
    # orig "a": two augmented copies (must share a group); orig "b": distinct image
    for split, name, seed, label in [
        ("train", "a_jpg.rf.111", 1, "1 0.5 0.5 0.3 0.2\n2 0.5 0.5 0.9 0.9\n"),  # open_eyes + face(drop)
        ("valid", "a_jpg.rf.222", 2, "0 0.5 0.5 0.2 0.2\n"),                     # close_eyes
        ("train", "b_jpg.rf.333", 3, "1 0.5 0.5 0.01 0.01\n"),                   # too small
    ]:
        cv2.imwrite(str(root / split / "images" / f"{name}.jpg"), _img(seed))
        (root / split / "labels" / f"{name}.txt").write_text(label)
    (root / "data.yaml").write_text("names: ['close_eyes', 'open_eyes', 'face', 'yawn']\n")
    external.upsert_source(f"roboflow/{proj}", {
        "kind": "roboflow", "project": proj, "version": 1, "location": f"raw/roboflow/{proj}/v1",
        "class_names": ["close_eyes", "open_eyes", "face", "yawn"], "licence": "CC BY 4.0",
    })
    mrl = paths.mrl_eye_raw_dir() / "mrlEyes_2018_01"
    for s in range(1, 6):
        (mrl / f"s{s:04d}").mkdir(parents=True)
        for st in (0, 1):
            cv2.imwrite(str(mrl / f"s{s:04d}" / f"s{s:04d}_0000{st}_0_0_{st}_0_1_01.png"),
                        _img(10 * s + st, 24)[..., 0])
    external.upsert_source("mrl_eye", {"kind": "mrl_eye", "location": "raw/mrl_eye/mrlEyes_2018_01",
                                       "licence": "unverified", "classes": {"closed": 5, "open": 5}})
    monkeypatch.setattr(config, "IR_REF_SAMPLE_IMAGES", 10)


def test_build_merges_classes_groups_and_zips(env, monkeypatch):
    paths, external, pd_ = env
    import pandas as pd
    _fake_sources(paths, external, monkeypatch)
    summary = pd_.build()
    df = pd.read_csv(paths.parts_dataset_csv(), dtype=str, keep_default_na=False)

    rf = df[df.source == "roboflow"]
    assert sorted(rf["class"]) == ["closed_eye", "open_eye"]          # "face" dropped
    assert rf.group_id.nunique() == 1                                  # both copies of "a" grouped
    assert rf.split.nunique() == 1
    assert summary["too_small"] == 1
    assert set(df["class"]) <= {"open_eye", "closed_eye", "yawn"}
    for p in rf.rgb_path.tolist() + rf.ir_path.tolist():
        assert (paths.dataset_root() / p).exists()
    ir_img = cv2.imread(str(paths.dataset_root() / rf.ir_path.iloc[0]), cv2.IMREAD_UNCHANGED)
    assert ir_img.ndim == 2                                            # IR copy is grayscale

    mrl = df[df.source == "mrl_eye"]
    assert len(mrl) == 10 and set(mrl.split) == {"train", "valid", "test"}
    assert (mrl.groupby("group_id").split.nunique() == 1).all()       # subject never split

    cmap = pd.read_csv(paths.parts_class_map_csv())
    assert set(cmap.loc[cmap.raw_class == "face", "class"]) == {"DROP"}

    z = zipfile.ZipFile(pd_.export_zip())
    zdf = pd.read_csv(io.BytesIO(z.read("parts_dataset.csv")), dtype=str, keep_default_na=False)
    names = set(z.namelist())
    assert all(p in names for c in ("rgb_path", "ir_path") for p in zdf[c] if p)
    assert {"README.txt", "class_map.csv", "ir_ref_hist.npy"} <= names


def test_unmapped_class_is_an_error(env, monkeypatch):
    paths, external, pd_ = env
    _fake_sources(paths, external, monkeypatch)
    from argus_dataset import config
    monkeypatch.setitem(config.ROBOFLOW_CLASS_MAP, "drowsiness-hgjay", {"open_eyes": "open_eye"})
    with pytest.raises(SystemExit, match="missing from ROBOFLOW_CLASS_MAP"):
        pd_.build()
