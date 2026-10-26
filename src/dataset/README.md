# `src/dataset/` — data extraction & dataset creation

This module turns **raw drowsiness-labelled video** into the **CSV / JPEG training artifacts**
the Colab training notebooks consume. It's a local, CPU-parallel, **pausable/resumable**
reimplementation of Argus's four Colab dataset-creation notebooks — run it on the WSL2 / Linux
dev box instead of fighting Colab timeouts, then upload the results to Drive.

```
 SOURCE                    RAW TREE                     BUILD                       ARTIFACT                        TRAINS

 UTA-RLDD zips  ─┐                                 build_lstm_windows.py       →  lstm_windows.csv            →  notebook 03  (LSTM)
                 ├─►  raw/raw_videos/          ─►  build_frame_features.py     →  frame_features(_enriched)   →  notebook 04/05  (RF / DNN)
 webcam /        │      subject_NN/                build_face_crops.py         →  face_crops/*.jpg + index    →  notebook 07  (CNN)
 phone clips   ──┘      level_<1-2>_clip_NN.mp4    build_cnn_lstm_windows.py   →  cnn_lstm_windows_index.csv  →  notebook 10/11  (CNN+LSTM)

                                                  then:  verify_artifacts.py  →  publish_to_drive.py  (rclone → Google Drive)
```

| build script | notebook it replaces | output under `processed/` |
|---|---|---|
| `scripts/build_lstm_windows.py` | `01_dataset_creation_lstm` | `lstm_windows.csv` |
| `scripts/build_frame_features.py` | `02_dataset_creation_flat` | `frame_features.csv` (+ `frame_features_enriched.csv` with `--enrich`) |
| `scripts/build_face_crops.py` | `06_dataset_creation_face_crops` | `face_crops/*.jpg` + `face_crops_index.csv` |
| `scripts/build_cnn_lstm_windows.py` | `09_dataset_creation_cnn_lstm` | `cnn_lstm_windows_index.csv` |

**These scripts are the source of truth for dataset creation now.** The notebooks are kept as
Colab-runnable reference. Every tunable that must match a notebook lives in
`argus_dataset/config.py`, annotated with the notebook + cell it mirrors. For architecture and
the *why*, read `CLAUDE.md` next to this file.

---

## 1. Setup (one time)

```bash
# System shared libs MediaPipe loads at import (same list as src/cv-argus/Dockerfile).
# Symptom if missing: "libGLESv2.so.2: cannot open shared object file" on the first build.
sudo apt install libgl1 libglib2.0-0 libsm6 libxext6 libxrender1 libgles2 libegl1 ffmpeg
# ffmpeg is only used by the UTA-RLDD extractor (step 2a), not the builds.

# WSL RAM defaults to ~15 GiB; raise it in Windows %UserProfile%\.wslconfig:
#   [wsl2]
#   memory=48GB
# then:  wsl --shutdown   (and reopen the terminal)

cd src/dataset
uv venv --python 3.12 .venv          # or: python3 -m venv .venv  (needs python3.12-venv)
. .venv/bin/activate
uv pip install -e . -r requirements.txt      # mediapipe + opencv + numpy + pandas + tqdm; NO tensorflow
```

**Data root.** Everything (`raw/`, `processed/`, `models/`) lives under `src/dataset/` by
default. Override with `export ARGUS_DATASET_ROOT=/somewhere/on/ext4` — but **never point it at
`/mnt/c`** (Windows NTFS makes the ~100k-file `face_crops/` write crawl). None of it is checked
into git.

```bash
python scripts/fetch_models.py       # downloads face_landmarker.task + blaze_face_short_range.tflite -> models/
```

---

## 2. Get raw video into `raw/raw_videos/`

The builds read `raw/raw_videos/subject_NN/level_<1-2>_clip_NN.mp4` directly and expect it
**already binary-labelled**: `level_1` = Not Drowsy, `level_2` = Drowsy. There is no relabel
step here. Two ways to populate it:

### 2a. Extract clips from UTA-RLDD (the bulk of the current dataset)

The [UTA Real-Life Drowsiness Dataset](https://sites.google.com/view/utarldd/home) is
downloaded as per-fold zips from its
[Kaggle mirror](https://www.kaggle.com/datasets/rishab260/uta-reallife-drowsiness-dataset).
`src/cv-argus/scripts/extract_uta_rldd_clips.py` cuts short random sub-clips from each ~10-min
source video, straight out of the zip (never unpacking the whole archive), into
`subject_NN/level_<1-3>_clip_N.mp4` — continuing Argus's `subject_NN` numbering, resumable
across runs via `subject_assignments.json`.

```bash
python ../cv-argus/scripts/extract_uta_rldd_clips.py --zip ~/Downloads/Fold1_part1.zip --output /tmp/uta_out
```

It emits **3-class** `level_1` (Alert) / `level_2` (Low Vigilant) / `level_3` (Drowsy) —
UTA-RLDD's native scheme. Collapse to this module's binary scheme before copying in:
`level_1` + `level_2` → `level_1`, `level_3` → `level_2`. The builds will refuse a `level_3`
filename with a pointed error (`config.map_level`).

### 2b. Collect new clips — `scripts/collect_clips.py`

Records from a webcam, or imports existing phone / dashcam files, into the raw tree with the
right name. Clip numbers are always `max(existing) + 1` per (subject, label) → it **never
overwrites**. Every clip is appended to `raw/raw_videos/collection_log.csv` (provenance; the
builds ignore it) the moment it lands.

```bash
# record two 20 s "Drowsy" clips for a brand-new subject from camera 0
python scripts/collect_clips.py --subject new --label 2 --count 2 --duration 20

# import existing files as "Not Drowsy" clips for subject_07
python scripts/collect_clips.py --subject 7 --not-drowsy --from-file ~/vids/*.mp4
python scripts/collect_clips.py --subject 7 --not-drowsy --from-dir ~/vids --reencode --import-fps 20

python scripts/collect_clips.py --list      # inventory: clips per subject per label
python scripts/collect_clips.py ... --dry-run
```

- `--subject` accepts `7`, `07`, `subject_07`, or `new` (next free `subject_NN` — real drivers
  continue the same numbering, no separate namespace).
- Label via `--label {1,2}`, `--drowsy`, or `--not-drowsy`.
- Imports are copied verbatim unless `--reencode` / `--import-fps` is passed.
- After each clip it runs a quick BlazeFace check and warns if a face isn't visible in most
  sampled frames (`--no-face-check` to skip).
- Webcam recording works **headless** (fixed `--duration`; Ctrl-C stops early and keeps the
  clip). For a live preview window: `pip install -e .[collect]` (installs the full OpenCV
  build — don't keep `opencv-python-headless` alongside it, see `pyproject.toml`).

---

## 3. Build the dataset

Each build is independent **except** `build_cnn_lstm_windows.py`, which reads the face crops.

```bash
python scripts/build_lstm_windows.py            # FaceLandmarker -> sliding windows, zero-pre-padded to 30 timesteps
python scripts/build_frame_features.py --enrich # FaceLandmarker -> one row per valid frame (+ temporal rolling features)
python scripts/build_face_crops.py             # BlazeFace -> cropped face JPEGs + index
python scripts/build_cnn_lstm_windows.py        # per-crop geometry -> windowed image-sequence index  (needs step above)

python scripts/verify_artifacts.py             # schema / label / relationship checks
python scripts/publish_to_drive.py             # rewrite crop paths, tar face_crops/, rclone to Drive
```

Or the whole chain in order: **`scripts/run_all.sh`** (`--reset`, `--workers N`,
`--only 01,02,06,09` supported; a single Ctrl-C pauses the running step, re-run to resume).

**Verify before publishing.** `verify_artifacts.py` asserts the *contract* the training
notebooks rely on (column names/order, label domain, per-file relationships) — **not** value
equality with a Colab run (MediaPipe isn't cross-platform deterministic; see `CLAUDE.md`). If
you have a Colab CSV handy: `verify_artifacts.py --compare path/to/colab.csv`.

**Publish.** `publish_to_drive.py` needs `rclone` configured with a Google Drive remote and
`export ARGUS_RCLONE_REMOTE=gdrive:Argus/dataset/dataset_processed`; without them it stages the
files and prints the manual `rclone copy` commands.

---

## 4. Add more data later (incremental — nothing is regenerated)

**The four builds are already incremental.** Each keeps
`processed/.progress/<artifact>.completed.jsonl`; re-running a `build_*.py` processes only clips
absent from that log and *appends* to the CSV — it never regenerates rows it already has. So
the update loop is just: **add clips → re-run the build (or `run_all.sh`)**. `--reset` is only
for a `config.py` change, never for new data.

`scripts/update_dataset.py` is the cross-artifact view around that:

```bash
python scripts/update_dataset.py            # status table: per artifact -> done / new / orphan
python scripts/update_dataset.py --run      # run every build that has new work (add --enrich for the flat one)
python scripts/update_dataset.py --prune    # drop rows whose raw .mp4 was deleted/renamed, then --run again
```

"orphan" = a CSV row / completed-log entry whose raw `.mp4` was deleted or renamed. `--prune`
removes those (and the orphaned `face_crops/*.jpg`); re-run `build_cnn_lstm_windows.py`
afterwards to rebuild the window index.

---

## 5. Pause / resume

Long runs are built to be interrupted:

- **Ctrl-C once** — stops accepting new clips, lets in-flight ones finish (each clip commits
  atomically), saves progress, prints how to resume. Ctrl-C again within 3 s force-quits.
- **Machine shutdown** — same handling on `SIGTERM`; anything a killed worker left half-written
  is detected and dropped next run (`reconcile: dropped N orphan rows`).
- **Resume** — re-run the exact same command. Completed clips are skipped.
- `--status` — show progress without doing work.
- `--reset` — throw the artifact + its progress away and start clean.
- `--force` — resume even though `config.py` changed since the run began (normally refused —
  mixing rows built under different settings corrupts the artifact).

Smoke testing: `--subjects subject_07,subject_08`, `--limit N`, `--dry-run` on any `build_*`.

---

## 6. Tuning

`--workers N` overrides the RAM/CPU-aware default (~24 at 48 GiB, self-limits to ~6–12 at
15 GiB). Each worker is pinned to one BLAS/OpenMP thread and kept off the GPU — the parallelism
is across clips, spawned (not forked) so MediaPipe's native thread pools don't leak into
children.

---

## 7. Troubleshooting

| symptom | cause / fix |
|---|---|
| `libGLESv2.so.2: cannot open shared object file` | missing system libs — run the `apt install` in step 1 |
| `BrokenProcessPool: ... terminated abruptly` | should not happen (this module uses `spawn`); if it does, lower `--workers` |
| `face_crops/` write is glacial | data root is on `/mnt/c` — move `ARGUS_DATASET_ROOT` onto ext4 |
| `level 3 in filename` error | raw tree still 3-class — collapse to `level_1`/`level_2` (step 2a) |
| build says "Nothing to do" after adding clips | the new files aren't named `subject_NN/level_<1-2>_clip_NN.mp4`, or aren't `.mp4` |
| `collect_clips.py` preview does nothing | `opencv-python-headless` has no GUI — records headless anyway; `pip install -e .[collect]` for the window |

---

## 7b. External datasets for the "parts" architecture (download-only)

Experimental eye/mouth-crop architecture (tiny grayscale CNNs → temporal head). These scripts
only **pull and catalogue** the external labelled sets into `raw/`. No cropping or relabelling
happens here, and nothing touches the existing 01/02/06/09 artifacts. Each source gets an entry
in `raw/datasets_manifest.json` (class list, counts, licence, checksum), and a rerun skips any
source whose checksum still matches.

```bash
python scripts/fetch_mrl_eye.py                 # MRL Eye: ~342 MB zip, 84,898 IR eye crops, 37 subjects
python scripts/fetch_roboflow_parts.py --list   # the 6 Roboflow Universe projects (config.ROBOFLOW_PROJECTS)
ROBOFLOW_API_KEY=... python scripts/fetch_roboflow_parts.py   # YOLO exports -> raw/roboflow/<project>/v<N>/
python scripts/datasets_status.py --classes     # table + on-disk check + each source's classes
```

- **Roboflow** needs a free API key (https://app.roboflow.com/settings/api), passed in the
  environment and never committed. `version: None` in `config.ROBOFLOW_PROJECTS` means "latest".
  The resolved version is written to the manifest; pin it in the config afterwards.
- **Licences:** MRL's page states none (cite Fusek, ISVC 2018), so it's recorded as `unverified`.
  Roboflow licences are read from each export's `README.roboflow.txt`.
- Review each project's class list (`--classes`) before writing any label mapping. The 11-class
  project especially needs care.

### Merge into one 3-class dataset + IR filter + zip

```bash
python scripts/build_parts_dataset.py --preview --zip   # ~1 min build; zip ~1.2 GB
```

This merges MRL and the five eye/yawn Roboflow projects (head-pose-only sources are skipped) into `open_eye` / `closed_eye` / `yawn`, using
`config.ROBOFLOW_CLASS_MAP`. Each project's mapping was decided from contact sheets of its real
boxes, not from class names: several projects use the same name for whole-face boxes. Output
goes to `processed/parts_dataset/`:

- **`parts_dataset.csv`**: the single labels file, one row per crop across every source.
- **`class_map.csv`**: what every source label became (or `DROP`).
- **`rgb/<class>/`**: the colour crops.
- **`ir/<class>/`**: IR-simulated grayscale crops, for the Pi NoIR camera. The simulation is a
  red-dominant channel mix, then a histogram match toward real-IR MRL. MRL itself is already IR
  and is left as-is.
- **`preview/`**: RGB-vs-IR contact sheets.
- **`processed/parts_dataset.zip`**: everything above, self-contained, with zip-relative paths
  and a README listing licences.

**Splits** are assigned per `group_id`. A group is an original image together with its Roboflow
augmented copies and any near-duplicate frames (matched by dHash, which also catches
consecutive video frames); for MRL a group is one subject. Copies therefore never straddle
train, valid and test. Don't re-split by row.

### Detection dataset, one YOLO-format zip for notebooks 12/13/14

This is the dataset for every detector notebook; it supersedes `parts_dataset.zip` for that
purpose. Images are **square face crops** (BlazeFace box + 25% margin, padded with black where it
leaves the frame, 192 px), plus "close-up" images (MRL, Roboflow eye/mouth close-ups) whose box
is the whole image.

Classes: `open_eye`, `closed_eye`, `yawn`, `head_straight`, `head_down`.

```bash
python scripts/fetch_models.py                                    # MediaPipe bundles (once)
python scripts/build_detection_dataset.py --mediapipe-only        # slow pass, resumable (~1 min / 8 workers)
python scripts/build_detection_dataset.py --sheet --zip           # build + preview + processed/parts_det.zip
```

The output lands in `processed/parts_det/`:
- `gray/` and `ir/`, each laid out as `{train,valid,test}/{images,labels}`;
- `data_gray.yaml` and `data_ir.yaml`, both Ultralytics-ready;
- `index.csv`, with one row per *source* image, including rejected ones and the reason.

The build does the following:

- **Head boxes** come from FaceLandmarker pitch/yaw (straight: |pitch| ≤ 18° and |yaw| ≤ 22°;
  down: pitch ≥ 20°; otherwise no head box). The sign was checked on real faces: positive
  means looking down. Where a source has **human head-pose labels**
  (`config.ROBOFLOW_HEAD_CLASS_MAP`, e.g. `headpose-w4zuc`), the human label wins over the pitch
  guess; `index.csv`'s `head_source` (`human` / `human_box` / `landmarks`) and `lm_head_class`
  record which one was used and what the pitch rule said, and the build summary prints the
  human-vs-pitch agreement.

**Head-pose sources** (`config.ROBOFLOW_HEADPOSE_PROJECTS`: `vidyashree/headpose-w4zuc`,
`karthik-madhvan/drowsiness-detection-xsriz`, `mdulo-v/facial-movements-detection`, and since
2026-10-26 `testproject-8jvqn/kantuk-edo70`, `head-movement/headmovement`,
`universe-adweg/noddingoff-z8zad`) are there for their head labels. To add another one:

```bash
ROBOFLOW_API_KEY=... python scripts/fetch_roboflow_parts.py --projects <project>
python scripts/build_detection_dataset.py --label-sheet <project>   # -> processed/label_sheet_<project>.jpg
# look at the sheet, then map each label in config.py:
#   ROBOFLOW_HEAD_CLASS_MAP  head labels -> head_straight / head_down / None (turned, up)
#   ROBOFLOW_CLASS_MAP       eye/mouth boxes -> open_eye / closed_eye / yawn, anything else -> None
#   ROBOFLOW_MOUTH_LABELS    optional: labels that say the mouth is closed / open / a yawn
# (the build's error prints a suggested head map from the names; check it against the sheet)
python scripts/build_detection_dataset.py --mediapipe-only && python scripts/build_detection_dataset.py --sheet --zip
```

When BlazeFace misses a strongly turned or lowered head, the crop is made around the human head
box instead (`head_source = human_box`, margin `DET_HEAD_BOX_MARGIN_FRAC` or the per-project
`DET_HEAD_BOX_MARGIN_BY_PROJECT`), and the image is kept even without eye boxes
(`DET_HEADPOSE_ALLOW_INCOMPLETE_EYES`). A human head label only counts if its box is at least half
the face's size (`DET_HUMAN_HEAD_MIN_REL`). For a dataset whose labels are no use at all (just
faces), list it in `ROBOFLOW_LANDMARK_ONLY_PROJECTS`: every box then comes from the landmarks.
For a source with noisy head labels, list it in `ROBOFLOW_HEAD_PITCH_CHECKED_PROJECTS`: a human
`head_down` then needs pitch ≥ 15° and a human `head_straight` needs the landmarks to agree, or
the image is rejected (`head_label_disagrees`). For a source whose box isn't a head box (e.g. the
whole driver), list it in `ROBOFLOW_IMAGE_LEVEL_HEAD_PROJECTS`: the label goes on the face
BlazeFace finds, and an image with no face is rejected. For a source whose Roboflow version uses
per-box brightness augmentation (`bbbrightness`, a bright/dark rectangle on the face), list it in
`ROBOFLOW_BOX_BRIGHTNESS_AUGMENTED_PROJECTS`: images showing that rectangle are rejected
(`box_brightness_artifact`).
- **Unlabelled eyes are pseudo-labelled** from landmarks (EAR < 0.15 closed, > 0.25 open,
  otherwise the image is rejected; a head-pose source's `head_down` image is kept with that eye
  unlabelled, since downcast lids are exactly what reads as unclear). Without this, eyes the
  annotators skipped (e.g. on yawn-only images) would be learned as background. Thresholds were
  calibrated on 4,071 human-labelled eyes. No pseudo eyes on faces turned more than 40°
  (`DET_PSEUDO_EYE_MAX_YAW_DEG`): the far eye is hidden. Flagged in `index.csv` (`pseudo_eyes`).
- **Unboxed yawns are pseudo-labelled** from the FaceLandmarker `jawOpen` blendshape (> 0.7 → a
  yawn box 1.3 × 1.4 the lip box; calibrated on 2,355 human yawn boxes). Sources with no yawn
  labels also reject `jawOpen` 0.5–0.7 as unclear. Human mouth labels (`ROBOFLOW_MOUTH_LABELS`)
  beat `jawOpen`. Flagged in `index.csv` (`pseudo_yawn`).
- **Landmark retries.** When the standard crop gives no landmarks on a real face (wide yawns,
  turned heads), wider crops are tried at a lower confidence (`DET_LM_RETRIES`); `lm_attempt` in
  `index.csv` says which one worked.
- **Stale files are removed.** Each build deletes images and labels it didn't write, so an image
  whose group moved split (ranks shift when sources are added) can't linger in its old folder.
- **Ambiguous source labels** reject the whole image (`config.ROBOFLOW_REJECT_IMAGE_LABELS`).
- **Splits** are by group and stratified on yawn. See `detection_dataset.py` for the grouping
  rules, which were fixed after a filename-collision chain once merged 46% of the images into
  one group.

**Check the balance** after a build: boxes, images and independent groups (original photos /
MRL subjects) per class in train / valid / test, the split proportions, and which source each
class comes from:

```bash
pip install -e .[analysis]                       # matplotlib, once
python scripts/analyze_detection_dataset.py      # -> processed/parts_det_report/*.png + balance.csv
```

## 7c. Capture our own stills to label — `scripts/capture_frames.py`

The external sets are other people's cameras. This captures **full frames** of our own people
(one every second by default) to label for the detector. They're full frames, not crops, because
`build_detection_dataset.py` finds the face and makes the crop itself.

```bash
# guided: the person is prompted through 5 poses, 10 s each, with 3 un-saved seconds to change
#   straight / eyes_closed / head_down / head_down_eyes_closed / yawn
python scripts/capture_frames.py --subject new --guided --rounds 2      # ~2 min, ~100 stills
python scripts/capture_frames.py --subject 55 --guided --phases eyes_closed head_down --phase-sec 20

# free capture (a fixed time, or --duration 0 until Ctrl-C), with a tag of your choice
python scripts/capture_frames.py --subject 55 --tag driving --duration 120 --interval 0.5

# a still per second out of existing recordings (phone videos, collect_clips.py clips, ...)
python scripts/capture_frames.py --subject 55 --tag phone --from-file ~/vids/*.mp4

python scripts/capture_frames.py --list     # stills per subject per tag so far
```

Output: `raw/webcam_frames/subject_NN/<YYYYmmdd-HHMMSS>/subject_NN_<session>_0001_<tag>.jpg`,
plus a `frames.csv` per session (time, tag, size, and a `sharpness` score; low values are
motion-blurred and worth skipping). Add `--save-video` to keep the whole stream as `session.mp4`.

- **The tag is a hint, not a label.** It's what the person was asked to do; they still blink,
  look away or start a pose late. Label what each image actually shows.
- **Subject ids are shared with `raw/raw_videos/`.** `--subject new` takes the next free number
  across both, so someone recorded with `collect_clips.py` too keeps one id.
- Same webcam setup as `collect_clips.py`: in WSL2 attach it with `usbipd` first (no
  `/dev/video*` means "cannot open camera"). Without the `[collect]` extra there's no preview
  window; the prompts print in the terminal instead (with a beep on each pose change).

**From stills to training data, no labelling: `scripts/add_own_captures.py`.** For guided
sessions (or `--from-file` videos tagged `head_down` / `head_down_eyes_closed`), the tag plus the
landmarks are enough:

```bash
python scripts/add_own_captures.py                   # -> raw/roboflow/own-captures/v1/ + manifest entry
python scripts/build_detection_dataset.py --zip      # the new source is picked up like any other
```

- A still tagged head-down gets a `head_down` box (the BlazeFace face box) when its pitch is at
  least `OWN_HEAD_DOWN_MIN_PITCH_DEG` (15°; pitch alone only says `head_down` from 20°) or the
  landmarks are lost. Below that it's dropped, because the person hadn't lowered their head yet.
- Every other still gets no label: eyes, yawns and the head come from the landmarks.
- The output prints `pitch_by_tag`. On a new camera, check that the `straight` p95 sits below
  `--min-pitch` before trusting the floor.
- All of one subject's stills form **one group**: files are named `subject_NN_jpg.rf.<hash>.jpg`,
  so they never straddle train and test.
- The folder is rebuilt on every run, and the script needs no Docker, only this module's venv.
- With few subjects, a whole subject lands in a single split. Check `index.csv` (project
  `own-captures`) to see which one.

**Or label by hand** (any pose, the same path as any other Roboflow source, section 7b):

1. Create a private Roboflow project, upload the session folders, and box `open_eye`,
   `closed_eye`, `yawn`, `head_straight`, `head_down` (head boxes around the whole face; no head
   box for a turned head). Unboxed eyes and yawns get pseudo-labelled from landmarks.
2. Generate a version (no augmentation needed), add it to `config.ROBOFLOW_PROJECTS` and
   `ROBOFLOW_HEADPOSE_PROJECTS`, and map its labels in `ROBOFLOW_HEAD_CLASS_MAP` /
   `ROBOFLOW_CLASS_MAP`.
3. `fetch_roboflow_parts.py --projects <project>`, then `build_detection_dataset.py` as above.

**Before trusting test numbers from the hand-labelled path:** the build groups images by Roboflow
stem and exact duplicates, so stills taken a second apart of the same person are separate groups
and can land in train *and* test. Those stills should be grouped by the `subject_NN_` filename
prefix first (not done for this path; `add_own_captures.py` does it).

---

## 8. Tests

```bash
uv pip install -e .[dev]     # adds pytest + tensorflow (geometry equivalence) + pyarrow
pytest
```

`tests/test_geometry_equiv.py` checks `argus_dataset/geometry.py` (the NumPy reimplementation
of the notebooks' `tf.keras` `GeometricRatioFeatureLayer`) against the real layer to
`atol=1e-4` — this is what lets the whole pipeline run without TensorFlow.
`tests/test_collect.py` / `tests/test_update.py` cover the collector's no-overwrite naming and
the incremental new-vs-orphan diff; `tests/test_frames.py` covers `capture_frames.py`'s
one-still-per-interval loop (with a fake camera and clock) and video extraction. `pytest` without `[dev]` skips only the TF/scipy tests.
