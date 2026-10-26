"""Balance report for the detection dataset (``processed/parts_det/``): how many boxes, images and
independent groups each class has, how each class splits across train / valid / test, and which
source each class comes from. Counting is pure (``count_labels``) so it's unit-tested; the charts
are matplotlib PNGs for the report. Driven by ``scripts/analyze_detection_dataset.py``.

A "group" is ``index.csv``'s ``group_id`` (augmented copies + re-uploads of one original photo,
or one MRL subject), so groups are the honest count of independent examples."""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from . import config

SPLITS = ("train", "valid", "test")
# Validated categorical slots 1-3 (dataviz reference palette, all-pairs, light surface).
SPLIT_COLORS = {"train": "#2a78d6", "valid": "#eb6834", "test": "#1baf7a"}
SURFACE, INK, INK_2, GRID = "#fcfcfb", "#0b0b0b", "#52514e", "#e4e3df"


def read_labels(label_file: Path) -> list[str]:
    """Class names of one YOLO label file (class ids index ``config.DET_CLASSES``)."""
    if not label_file.exists():
        return []
    return [config.DET_CLASSES[int(line.split()[0])]
            for line in label_file.read_text().splitlines() if line.strip()]


def count_labels(index: pd.DataFrame, labels: dict[str, list[str]]) -> pd.DataFrame:
    """One row per (accepted image, class it contains): ``class``, ``split``, ``source``,
    ``group_id``, ``boxes`` (that class's boxes in the image). ``labels`` maps image_id → class
    names. The tables below aggregate it."""
    rows = []
    for r in index[index.status == "ok"].itertuples():
        names = labels.get(r.image_id, [])
        source = "mrl_eye" if r.source == "mrl_eye" else r.project
        for cls in set(names):
            rows.append({"class": cls, "split": r.split, "source": source, "group_id": r.group_id,
                         "boxes": names.count(cls)})
    return pd.DataFrame(rows, columns=["class", "split", "source", "group_id", "boxes"])


def _measure(counts: pd.DataFrame, keys: list[str], measure: str) -> pd.Series:
    """boxes = box sum, images = rows, groups = distinct group_ids (a dHash-joined group can hold
    images of two sources, so groups are never summed across sources)."""
    g = counts.groupby(keys)
    return {"boxes": g.boxes.sum, "images": g.size, "groups": g.group_id.nunique}[measure]()


def per_split(counts: pd.DataFrame, measure: str) -> pd.DataFrame:
    """class × split table of one measure, classes in DET_CLASSES order, zeros filled."""
    t = _measure(counts, ["class", "split"], measure).unstack(fill_value=0)
    return t.reindex(index=list(config.DET_CLASSES), columns=list(SPLITS), fill_value=0).astype(int)


def per_source(counts: pd.DataFrame, measure: str = "boxes") -> pd.DataFrame:
    """source × class table of one measure over all splits, biggest sources first."""
    t = _measure(counts, ["source", "class"], measure).unstack(fill_value=0)
    t = t.reindex(columns=list(config.DET_CLASSES), fill_value=0).astype(int)
    return t.loc[t.sum(axis=1).sort_values(ascending=False).index]


def load(base: Path) -> tuple[pd.DataFrame, dict[str, list[str]]]:
    """``index.csv`` and every accepted image's labels from a built ``parts_det/`` folder."""
    index = pd.read_csv(base / "index.csv")
    ok = index[index.status == "ok"]
    return index, {r.image_id: read_labels(base / r.label_path) for r in ok.itertuples()}


# --- charts ----------------------------------------------------------------------------------

def _style(ax, title: str, xlabel: str) -> None:
    ax.set_facecolor(SURFACE)
    ax.set_title(title, loc="left", color=INK, fontsize=12, pad=28)   # room for the legend row
    ax.set_xlabel(xlabel, color=INK_2)
    ax.tick_params(colors=INK_2, length=0)
    ax.grid(axis="x", color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)
    for s in ax.spines.values():
        s.set_visible(False)


def _stacked(ax, table: pd.DataFrame, percent: bool) -> None:
    """Horizontal bars stacked train/valid/test, a 2 px surface gap between segments."""
    data = table.div(table.sum(axis=1).where(lambda s: s > 0, 1), axis=0) * 100 if percent else table
    left = pd.Series(0.0, index=data.index)
    for split in SPLITS:
        ax.barh(data.index, data[split], left=left, color=SPLIT_COLORS[split], height=0.62,
                edgecolor=SURFACE, linewidth=2, label=split)
        if percent:   # every segment's share, in text ink, where it fits
            for y, (l, v) in enumerate(zip(left, data[split])):
                if v >= 7:
                    ax.text(l + v / 2, y, f"{v:.0f}%", ha="center", va="center", color="white",
                            fontsize=9, fontweight="bold")
        left += data[split]
    if not percent:   # total at each bar end
        for y, total in enumerate(table.sum(axis=1)):
            ax.text(total, y, f"  {total:,}", va="center", color=INK, fontsize=9)
        ax.set_xlim(0, table.sum(axis=1).max() * 1.15)
    else:
        ax.set_xlim(0, 100)
    ax.invert_yaxis()
    ax.legend(ncol=3, frameon=False, loc="lower left", bbox_to_anchor=(0, 1.0), labelcolor=INK_2,
              fontsize=9, handlelength=1, borderaxespad=0.2)


def plot_report(counts: pd.DataFrame, out_dir: Path) -> list[Path]:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    out_dir.mkdir(parents=True, exist_ok=True)
    plt.rcParams.update({"font.size": 10, "figure.facecolor": SURFACE, "savefig.facecolor": SURFACE})
    written = []

    for measure, what in (("boxes", "Boxes"), ("images", "Images containing the class"),
                          ("groups", "Independent groups (original photos / MRL subjects)")):
        t = per_split(counts, measure)
        fig, ax = plt.subplots(figsize=(9, 3.6))
        _stacked(ax, t, percent=False)
        _style(ax, f"{what} per class, by split", f"{measure}")
        fig.tight_layout()
        p = out_dir / f"{measure}_per_class.png"
        fig.savefig(p, dpi=150)
        plt.close(fig)
        written.append(p)

    fig, ax = plt.subplots(figsize=(9, 3.6))
    _stacked(ax, per_split(counts, "groups"), percent=True)
    _style(ax, "Split proportions per class (independent groups)", "% of the class's groups")
    fig.tight_layout()
    p = out_dir / "split_proportions.png"
    fig.savefig(p, dpi=150)
    plt.close(fig)
    written.append(p)

    src = per_source(counts, "boxes")
    fig, ax = plt.subplots(figsize=(9, 0.42 * len(src) + 1.6))
    ax.imshow(src.values, aspect="auto", cmap="Blues", vmin=0, vmax=max(1, src.values.max()))
    ax.set_xticks(range(len(src.columns)), src.columns, color=INK_2)
    ax.set_yticks(range(len(src.index)), src.index, color=INK_2)
    ax.tick_params(length=0)
    ax.xaxis.tick_top()
    half = src.values.max() / 2
    for (i, j), v in pd.DataFrame(src.values).stack().items():
        if v:
            ax.text(j, i, f"{v:,}", ha="center", va="center", fontsize=8.5,
                    color="white" if v > half else INK)
    for s in ax.spines.values():
        s.set_visible(False)
    ax.set_title("Boxes per class and source (all splits)", loc="left", color=INK, fontsize=12, pad=26)
    fig.tight_layout()
    p = out_dir / "source_by_class.png"
    fig.savefig(p, dpi=150)
    plt.close(fig)
    written.append(p)
    return written


def summary_table(counts: pd.DataFrame) -> pd.DataFrame:
    """class × (measure, split) wide table plus totals and each split's share of groups."""
    parts = {}
    for m in ("boxes", "images", "groups"):
        t = per_split(counts, m)
        t["total"] = t.sum(axis=1)
        parts[m] = t
    out = pd.concat(parts, axis=1)
    g = parts["groups"]
    for s in SPLITS:
        out[("groups_pct", s)] = (100 * g[s] / g["total"].where(g["total"] > 0, 1)).round(1)
    return out
