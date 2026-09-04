#!/usr/bin/env python3
"""Run on wopr only, invoked over SSH by find_duplicates.py.

Reads photo ids (one per line) from stdin, looks up each one's
full-res original in data/images/, and prints a JSON object mapping
id -> ISO timestamp (or null) to stdout.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, "src")
from vistarium.exif_util import exif_capture_datetime  # noqa: E402

ids = [line.strip() for line in sys.stdin if line.strip()]
out = {}
for photo_id in ids:
    path = Path("data/images") / f"{photo_id}.jpg"
    if path.exists():
        dt = exif_capture_datetime(path)
        out[photo_id] = dt.isoformat() if dt else None
print(json.dumps(out))
