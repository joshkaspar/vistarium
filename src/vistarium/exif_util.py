"""Deterministic time-of-day evidence: caption text and EXIF timestamps.

Both of these are stronger, cheaper evidence than a model's visual
guess when they're available -- the pipeline (see pipeline.py) checks
these first and only falls back to a model judgment call when neither
gives a confident answer. This is the concrete form of the project's
"deterministic first, model as narrow fallback" division of labor
applied specifically to time_of_day.
"""

from __future__ import annotations

import re
from pathlib import Path

from PIL import ExifTags, Image

_CAPTION_PATTERNS: dict[str, re.Pattern] = {
    "morning": re.compile(
        r"\b(morning|dawn|sunrise|daybreak|at dawn|sun rising|rising sun|early morning|sun up)\b",
        re.I,
    ),
    "afternoon": re.compile(r"\b(afternoon|midday|high noon|bright day|by day|noon)\b", re.I),
    "evening": re.compile(
        r"\b(evening|sunset|sundown|dusk|golden hour|setting sun"
        r"|at sunset|sunset glow|alpenglow)\b",
        re.I,
    ),
    "night": re.compile(
        r"\b(night|midnight|moonlight|moonlit|by moonlight|starlight|at night|nightfall"
        r"|under the stars|night sky|stars|milky way|full moon|aurora)\b",
        re.I,
    ),
}


def caption_time_of_day(text: str) -> str | None:
    """Return a confident morning/afternoon/evening/night bucket from
    caption/title/keyword text, or None if no pattern matched. If more than
    one bucket matches, this is ambiguous evidence, not confident evidence
    -- return None rather than guessing between them."""
    if not text:
        return None
    matches = {bucket for bucket, rx in _CAPTION_PATTERNS.items() if rx.search(text)}
    if len(matches) == 1:
        return matches.pop()
    return None


def hour_to_bucket(hour: int) -> str:
    """Map a 24-hour clock hour to a time_of_day bucket."""
    if 5 <= hour < 11:
        return "morning"
    if 11 <= hour < 17:
        return "afternoon"
    if 17 <= hour < 21:
        return "evening"
    return "night"


def _parse_hour(value) -> int | None:
    # EXIF datetime format: "YYYY:MM:DD HH:MM:SS"
    m = re.search(r"\d{4}:\d{2}:\d{2}\s+(\d{2}):\d{2}:\d{2}", str(value or ""))
    return int(m.group(1)) if m else None


def exif_capture_hour(path: Path) -> int | None:
    """Read the capture hour (0-23) from an image file's real EXIF data, or
    None if no usable timestamp tag is present.

    DateTimeOriginal/DateTimeDigitized (in the Exif SubIFD) are checked
    first, ahead of the base IFD0 DateTime tag -- DateTime is the file's
    last-modified timestamp, not capture time, and editing software
    routinely rewrites it on save. Found live in the 2026-08-30 validation
    checkpoint: a photo titled "Rosy Morning Light" had its real capture
    time (2017-12-03 07:03:21, from DateTimeOriginal) overwritten in IFD0's
    DateTime by a 2025 Photoshop re-save (2025-04-02 22:46:39), which this
    function used to read instead, producing an incorrect "night" bucket
    for a genuinely morning photo. See DECISIONS.md."""
    try:
        with Image.open(path) as img:
            exif = img.getexif()
            if not exif:
                return None
            sub_ifd = exif.get_ifd(ExifTags.IFD.Exif)
    except Exception:
        return None

    for tag_name in ("DateTimeOriginal", "DateTimeDigitized"):
        tag_id = next((k for k, v in ExifTags.TAGS.items() if v == tag_name), None)
        hour = _parse_hour(sub_ifd.get(tag_id)) if tag_id is not None else None
        if hour is not None:
            return hour

    return _parse_hour(exif.get(306))  # IFD0 DateTime -- last resort only
