"""argus_dataset — local, pausable/resumable ports of Argus's dataset-creation notebooks.

The four Colab notebooks under ``src/notebook/`` (``01_dataset_creation_lstm``,
``02_dataset_creation_flat``, ``06_dataset_creation_face_crops``,
``09_dataset_creation_cnn_lstm``) are reimplemented here as CPU-parallel local scripts.
**These scripts are the source of truth for dataset creation now**; the notebooks are kept as
Colab-runnable reference only.

The raw video tree (``raw/raw_videos/subject_NN/level_<1-2>_clip_NN.mp4``) is expected to be
binary-labelled already (level_1 = Not Drowsy, level_2 = Drowsy) — there is no separate
relabel step.

Every tunable that has to match the notebooks lives in :mod:`argus_dataset.config`, each one
annotated with the notebook and cell it mirrors.
"""

__version__ = "0.1.0"
