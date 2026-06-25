#!/usr/bin/env python3
"""Open native folder picker (Windows Explorer dialog). Prints chosen path to stdout."""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from chatxz.utils.folder_picker import pick_folder  # noqa: E402

start = sys.argv[1] if len(sys.argv) > 1 else None
picked = pick_folder(start)
if picked:
    print(picked)
    raise SystemExit(0)
raise SystemExit(1)