"""Shared argparse plumbing for the ``scripts/`` entry points."""

from __future__ import annotations

import argparse


def add_common_args(parser: argparse.ArgumentParser, *, video_build: bool = True) -> None:
    parser.add_argument("--workers", type=int, default=None,
                        help="worker processes (default: RAM/CPU-aware auto)")
    parser.add_argument("--status", action="store_true",
                        help="print resume progress and exit")
    parser.add_argument("--reset", action="store_true",
                        help="delete the artifact + its progress and rebuild from scratch")
    parser.add_argument("--force", action="store_true",
                        help="resume even though argus_dataset/config.py changed since the run began")
    parser.add_argument("--dry-run", action="store_true",
                        help="list what would be processed, do nothing")
    if video_build:
        parser.add_argument("--subjects", default=None,
                            help="comma-separated subject folder names to limit to, e.g. subject_07,subject_08")
        parser.add_argument("--limit", type=int, default=None,
                            help="process at most N clips (smoke testing)")


def subjects_list(args) -> list[str] | None:
    raw = getattr(args, "subjects", None)
    return [s.strip() for s in raw.split(",") if s.strip()] if raw else None
