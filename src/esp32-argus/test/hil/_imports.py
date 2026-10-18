"""Puts backend-argus's `app` and cv-argus's stdlib-only packages on the import path, straight
from their source trees, same as `edge_chain/conftest.py`. Import this module first."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[3]

if str(SRC / "backend-argus") not in sys.path:
    sys.path.insert(0, str(SRC / "backend-argus"))

_CV_ARGUS_SRC = SRC / "cv-argus" / "src"
if "cv_argus" not in sys.modules:
    _spec = importlib.util.spec_from_file_location(
        "cv_argus", _CV_ARGUS_SRC / "__init__.py", submodule_search_locations=[str(_CV_ARGUS_SRC)]
    )
    _module = importlib.util.module_from_spec(_spec)
    sys.modules["cv_argus"] = _module
    _spec.loader.exec_module(_module)
