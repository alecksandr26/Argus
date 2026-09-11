"""Lets the package run as `python -m cv_argus` (not just `python -m cv_argus.main`) —
the standard way to mark a Python package as directly runnable.
"""

import cv_argus.bootstrap  # noqa: F401 -- MUST be first: sets thread env vars before tf/cv2 load

from cv_argus.main import main

if __name__ == "__main__":
    main()
