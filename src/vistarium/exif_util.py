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


_DATETIME_TAG_IDS = {
    tag_id for tag_id, name in ExifTags.TAGS.items() if name in ("DateTimeOriginal", "DateTime")
}


def exif_capture_hour(path: Path) -> int | None:
    """Read the capture hour (0-23) from an image file's real EXIF data, or
    None if no usable timestamp tag is present."""
    try:
        with Image.open(path) as img:
            exif = img.getexif()
    except Exception:
        return None
    if not exif:
        return None
    for tag_id in _DATETIME_TAG_IDS:
        value = exif.get(tag_id)
        if not value:
            continue
        # EXIF datetime format: "YYYY:MM:DD HH:MM:SS"
        m = re.search(r"\d{4}:\d{2}:\d{2}\s+(\d{2}):\d{2}:\d{2}", str(value))
        if m:
            return int(m.group(1))
    return None
