# `src/dataset/` — local dataset creation

Local, CPU-parallel, **pausable/resumable** ports of Argus's four Colab dataset-creation
notebooks. Run these on the WSL2 box instead of fighting Colab timeouts; upload the results to
Drive for the training notebooks (`03`/`04`/`05`/`07`/`10`), which stay on Colab for the GPU.

| script | notebook it replaces | output |
|---|---|---|
| `scripts/build_lstm_windows.py` | `01_dataset_creation_lstm` | `processed/lstm_windows.csv` |
| `scripts/build_frame_features.py` | `02_dataset_creation_flat` | `processed/frame_features.csv` (+ `_enriched.csv` with `--enrich`) |
| `scripts/build_face_crops.py` | `06_dataset_creation_face_crops` | `processed/face_crops/*.jpg` + `face_crops_index.csv` |
| `scripts/build_cnn_lstm_windows.py` | `09_dataset_creation_cnn_lstm` | `processed/cnn_lstm_windows_index.csv` |

**These scripts are the source of truth for dataset creation now.** The notebooks are kept as
Colab-runnable reference. Every tunable that has to match a notebook lives in
`argus_dataset/config.py`, annotated with the notebook + cell it mirrors.

The raw video tree is `raw/raw_videos/subject_NN/level_<1-2>_clip_NN.mp4` and must already be
**binary-labelled** — `level_1` = Not Drowsy, `level_2` = Drowsy. There is no relabel step; the
builds read this tree directly. Use `scripts/collect_clips.py` (below) to get clips into it with
the right names.

## Setup (one time)

```bash
# shared libs MediaPipe loads at import (same list as src/cv-argus/Dockerfile). Symptom if
# missing: "libGLESv2.so.2: cannot open shared object file" the first time a build runs MediaPipe.
sudo apt install libgl1 libglib2.0-0 libsm6 libxext6 libxrender1 libgles2 libegl1 ffmpeg
# ffmpeg is only needed by src/cv-argus/scripts/extract_uta_rldd_clips.py, not the builds.

# WSL RAM defaults to ~15 GiB; raise it in Windows %UserProfile%\.wslconfig:
#   [wsl2]
#   memory=48GB
# then:  wsl --shutdown   (and reopen the terminal)

cd src/dataset
uv venv --python 3.12 .venv          # or: python3 -m venv .venv  (needs python3.12-venv)
. .venv/bin/activate
uv pip install -e . -r requirements.txt      # mediapipe + opencv + numpy + pandas + tqdm; NO tensorflow
```

The data root defaults to this directory (`src/dataset/`). Override with
`export ARGUS_DATASET_ROOT=/somewhere/on/ext4` — but never point it at `/mnt/c` (NTFS makes
the ~100k-file `face_crops/` write crawl).

## Run it

```bash
python scripts/fetch_models.py                       # MediaPipe bundles -> models/

# put your binary-labelled clips in raw/raw_videos/ (subject_NN/level_<1-2>_clip_NN.mp4).
# (extract_uta_rldd_clips.py emits 3-class level_<1-3>; collapse those to level_1/level_2
#  yourself before pointing the builds at them.)

python scripts/build_lstm_windows.py                 # each of these is independent...
python scripts/build_frame_features.py --enrich
python scripts/build_face_crops.py
python scripts/build_cnn_lstm_windows.py             # ...except this one needs the face crops

python scripts/verify_artifacts.py
python scripts/publish_to_drive.py                   # rewrite paths, tar crops, rclone to Drive
```

Or the whole chain: `scripts/run_all.sh`.

## Collecting new clips

`scripts/collect_clips.py` files clips into `raw/raw_videos/subject_NN/level_<1-2>_clip_NN.mp4`
with the correct name and a clip number that's always `max(existing) + 1` — it **never
overwrites**. Every clip is appended to `raw/raw_videos/collection_log.csv` (provenance;
not read by the builds) the moment it lands.

```bash
# record two 20 s "Drowsy" clips for a brand-new subject from camera 0
python scripts/collect_clips.py --subject new --label 2 --count 2 --duration 20

# import existing phone/dashcam files as "Not Drowsy" clips for subject_07
python scripts/collect_clips.py --subject 7 --not-drowsy --from-file ~/vids/*.mp4
python scripts/collect_clips.py --subject 7 --not-drowsy --from-dir ~/vids --reencode --import-fps 20

python scripts/collect_clips.py --list      # inventory: clips per subject per label
```

`--subject` takes `7`, `07`, `subject_07`, or `new` (next free `subject_NN`). Label via
`--label {1,2}`, `--drowsy`, or `--not-drowsy`. Imports are copied verbatim unless `--reencode`
/ `--import-fps` is given. After capture it runs a quick BlazeFace check and warns if a face
isn't visible in most frames (`--no-face-check` to skip).

Webcam recording works headless (fixed `--duration`, Ctrl-C stops early and keeps the clip).
For a live preview window install the full OpenCV build: `pip install -e .[collect]` (don't
keep `opencv-python-headless` alongside it — see `pyproject.toml`).

## Updating the dataset with new raw clips

**The four builds are already incremental.** Re-running any `build_*.py` processes only clips
not in `processed/.progress/<artifact>.completed.jsonl` and *appends* to the CSV — it never
regenerates rows it already has. So: collect clips, re-run the build (or `run_all.sh`), done.
`--reset` is only for a `config.py` change.

`scripts/update_dataset.py` is the cross-artifact view around that:

```bash
python scripts/update_dataset.py            # status table: per artifact -> done / new / orphan
python scripts/update_dataset.py --run      # run every build that has new work (+ --enrich)
python scripts/update_dataset.py --prune    # drop rows whose raw .mp4 was deleted/renamed
```

"orphan" = a CSV row / completed-log entry whose raw clip is gone. `--prune` removes those
(and the orphaned `face_crops/*.jpg`); re-run `build_cnn_lstm_windows.py` afterwards to rebuild
the window index.

## Pause / resume

Long runs are built to be interrupted:

- **Ctrl-C once** — the run stops accepting new clips, lets the in-flight ones finish (each
  clip commits atomically), saves progress, and prints how to resume. Ctrl-C again within 3 s
  force-quits.
- **Shut the machine down** — same handling on `SIGTERM`; anything a killed worker left
  half-written is detected and dropped on the next run (`reconcile: dropped N orphan rows`).
- **Resume** — just re-run the exact same command. Clips already in
  `processed/.progress/<artifact>.completed.jsonl` are skipped.
- `--status` — show progress without doing work.
- `--reset` — throw the artifact + its progress away and start clean.
- `--force` — resume even though `config.py` changed since the run began (normally refused —
  mixing rows built under different settings corrupts the artifact).

Smoke testing: `--subjects subject_07,subject_08` and `--limit N` on any `build_*` script,
plus `--dry-run`.

## Tuning

`--workers N` overrides the auto default (RAM- and CPU-aware; ~24 with 48 GiB, self-limits to
~6–12 at 15 GiB). Each worker is pinned to one BLAS/OpenMP thread and kept off the GPU — the
parallelism is across clips.

## Tests

```bash
uv pip install -e .[dev]     # adds pytest + tensorflow (for the geometry equivalence check)
pytest
```

`tests/test_geometry_equiv.py` checks `argus_dataset/geometry.py` (the NumPy reimplementation
of the notebooks' `tf.keras` `GeometricRatioFeatureLayer`) against the real layer to
`atol=1e-4` — this is what lets the pipeline run without TensorFlow.
