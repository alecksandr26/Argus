"""`cv_argus.model` imports its downloader (and so `gdown`) only when a download name is used,
so importing the rule engine doesn't need `gdown`."""

import subprocess
import sys

import pytest


def _run(code):
    return subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=120)


def test_importing_the_rule_engine_does_not_import_gdown():
    r = _run("import sys\n"
             "import cv_argus.model\n"
             "import cv_argus.orchestrator.parts_rules\n"
             "assert 'gdown' not in sys.modules, 'gdown imported'\n"
             "assert 'cv_argus.model.downloader' not in sys.modules\n"
             "print('ok')")
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == "ok"


def test_download_names_still_resolve_from_the_package():
    from cv_argus import model
    from cv_argus.model import downloader

    assert model.download_parts_int8_model is downloader.download_parts_int8_model
    assert model.download_parts_float_model is downloader.download_parts_float_model
    assert set(model.__all__) == {"download_parts_int8_model", "download_parts_float_model",
                                  "DetectionResult"}


def test_unknown_names_still_raise_attribute_error():
    from cv_argus import model

    with pytest.raises(AttributeError):
        model.no_such_name  # noqa: B018
