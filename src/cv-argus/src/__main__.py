"""Lets the package run as `python -m cv_argus` (not just `python -m cv_argus.main`) —
the standard way to mark a Python package as directly runnable.
"""

from cv_argus.main import main

if __name__ == "__main__":
    main()
