#!/usr/bin/env python3
"""src/notebook/02_dataset_creation_flat.ipynb -> ``processed/frame_features.csv``
(one row per valid sampled frame) and, with ``--enrich``, ``frame_features_enriched.csv``
(+ EAR_mean, causal rolling mean/std over 5 & 15 frames, and first-difference deltas).

Pausable/resumable like the other builds. ``--analysis`` prints a Spearman |r| ranking of each
feature against the label (needs no extra deps; Kruskal-Wallis / logistic-regression baselines
from the notebook need ``pip install -e .[analysis]`` and are left to Colab).
"""
import argparse

import argus_dataset.bootstrap  # noqa: F401
from argus_dataset import assets, cli, config, paths, pipelines, windowing, workers


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    cli.add_common_args(ap)
    ap.add_argument("--enrich", action="store_true", help="also write frame_features_enriched.csv")
    ap.add_argument("--analysis", action="store_true", help="print a Spearman |r| feature ranking")
    args = ap.parse_args()

    if not (args.status or args.dry_run):
        assets.ensure_face_landmarker()
    pipeline = pipelines.FaceLandmarkerFeatureExtractor(paths.face_landmarker_path())

    code = workers.run_video_build(
        "frame_features", paths.frame_features_csv(), config.FLAT_CSV_COLUMNS, pipeline,
        workers=args.workers, subjects=cli.subjects_list(args), limit=args.limit,
        dry_run=args.dry_run, reset=args.reset, force=args.force, status=args.status,
    )
    if code != 0 or args.status or args.dry_run:
        raise SystemExit(code)

    if args.enrich:
        import pandas as pd
        df = pd.read_csv(paths.frame_features_csv())
        out = paths.frame_features_enriched_csv()
        windowing.enrich_frame_features(df).to_csv(out, index=False)
        print(f"enriched -> {out.name}  ({out.stat().st_size / 1e6:.1f} MB)")

    if args.analysis:
        _spearman_ranking()

    raise SystemExit(0)


def _spearman_ranking() -> None:
    import pandas as pd
    df = pd.read_csv(paths.frame_features_csv())
    feats = [c for c in config.FEATURE_COLUMN_NAMES if c in df.columns]
    corr = df[feats + ["level"]].corr(method="spearman")["level"].drop("level")
    print("\nSpearman |r| vs level (top 15):")
    for name, val in corr.abs().sort_values(ascending=False).head(15).items():
        print(f"  {name:24s} {val:.3f}")


if __name__ == "__main__":
    main()
