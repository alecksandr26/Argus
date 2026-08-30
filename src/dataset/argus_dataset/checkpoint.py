"""Pause / interrupt / resume state for a build.

Each build (``lstm_windows`` / ``frame_features`` / ``face_crops`` / ``cnn_lstm_windows``) has:

  * ``processed/.progress/<artifact>.completed.jsonl`` — one JSON array per line, the
    ``[subject, parent_video]`` key of every clip **fully committed** to the artifact CSV. This
    is the authority for "what's done": a worker appends to it in the *same* lock-held block as
    it appends the clip's rows + ``fsync``s both, so the two can't diverge except in a
    sub-millisecond window that :meth:`RunCheckpoint.reconcile` repairs.
  * ``processed/.progress/<artifact>.json`` — a human-readable progress summary (counts, the
    ``config_hash`` the run was started under, failures), rewritten atomically.

A run is resumable by re-issuing the same command. ``--reset`` throws it all away and starts
clean; ``--force`` proceeds even when ``config.py`` changed since the run began.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pandas as pd

from . import config, paths

Key = tuple[str, str]


class ResumeConfigMismatch(RuntimeError):
    pass


class RunCheckpoint:
    def __init__(self, artifact: str):
        self.artifact = artifact
        self.config_hash = config.config_hash(artifact)
        pdir = paths.progress_dir()
        self.progress_path = pdir / f"{artifact}.json"
        self.completed_log = pdir / f"{artifact}.completed.jsonl"

    # --- completed-clip tracking ----------------------------------------------------------

    def completed_keys(self) -> set[Key]:
        if not self.completed_log.exists():
            return set()
        out: set[Key] = set()
        for line in self.completed_log.read_text().splitlines():
            line = line.strip()
            if line:
                out.add(tuple(json.loads(line)))
        return out

    def append_completed(self, key: Key) -> None:
        """Append one key. Callers hold the shared CSV lock around this + the CSV append."""
        with open(self.completed_log, "a") as fh:
            fh.write(json.dumps(list(key)) + "\n")
            fh.flush()
            os.fsync(fh.fileno())

    # --- resume guards ------------------------------------------------------------------

    def load_progress(self) -> dict:
        if self.progress_path.exists():
            return json.loads(self.progress_path.read_text())
        return {}

    def check_config_or_die(self, force: bool) -> None:
        prev = self.load_progress().get("config_hash")
        if prev and prev != self.config_hash and not force:
            raise ResumeConfigMismatch(
                f"{self.artifact}: this run's config_hash is {self.config_hash} but the "
                f"in-progress artifact was built under {prev}. argus_dataset/config.py changed "
                f"in a way that affects '{self.artifact}'. Re-run with --reset to rebuild from "
                f"scratch, or --force to append anyway (mixes incompatible rows — not advised)."
            )

    def reconcile(self, csv_path: Path, key_cols: tuple[str, str]) -> int:
        """Drop rows whose ``key_cols`` value isn't in ``completed_keys()`` — orphans from a
        clip whose CSV append landed but whose completed-log line didn't (interrupt mid-commit).
        Returns the number of rows removed."""
        if not csv_path.exists() or csv_path.stat().st_size == 0:
            return 0
        done = self.completed_keys()
        df = pd.read_csv(csv_path)
        if df.empty:
            return 0
        keep_mask = df[list(key_cols)].apply(lambda r: (r.iloc[0], r.iloc[1]) in done, axis=1)
        removed = int((~keep_mask).sum())
        if removed:
            df.loc[keep_mask].to_csv(csv_path, index=False)
        return removed

    # --- progress file ------------------------------------------------------------------

    def save_progress(self, *, total_units: int, n_completed: int,
                      failed: list[tuple[Key, str]], started_at: float) -> None:
        payload = {
            "artifact": self.artifact,
            "config_hash": self.config_hash,
            "started_at": started_at,
            "updated_at": time.time(),
            "total_units": total_units,
            "n_completed": n_completed,
            "n_failed": len(failed),
            "failed": [[list(k), reason] for k, reason in failed],
        }
        tmp = self.progress_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, indent=2))
        tmp.replace(self.progress_path)

    def status_report(self, total_units: int | None = None) -> str:
        p = self.load_progress()
        done = len(self.completed_keys())
        if not p and done == 0:
            return f"{self.artifact}: no run recorded yet."
        total = total_units if total_units is not None else p.get("total_units", "?")
        lines = [
            f"{self.artifact}:",
            f"  completed clips : {done} / {total}",
            f"  failures        : {p.get('n_failed', 0)}",
            f"  config_hash     : {p.get('config_hash', '?')}"
            + ("  (CURRENT)" if p.get("config_hash") == self.config_hash else "  (STALE — config.py changed)"),
        ]
        if p.get("updated_at"):
            lines.append(f"  last updated    : {time.ctime(p['updated_at'])}")
        for k, reason in p.get("failed", [])[:10]:
            lines.append(f"    ! {'/'.join(k)}: {reason}")
        return "\n".join(lines)

    # --- reset -------------------------------------------------------------------------

    def reset(self, extra_paths: tuple[Path, ...] = ()) -> None:
        for pth in (self.progress_path, self.completed_log, *extra_paths):
            if pth.is_dir():
                import shutil
                shutil.rmtree(pth)
            elif pth.exists():
                pth.unlink()
