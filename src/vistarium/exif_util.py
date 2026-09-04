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
from datetime import datetime
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


# Dates cameras fall back to when their clock battery has died or was never
# set at all -- not a real capture time, even though it parses as a valid
# one. Found live in the 2026-08-30 Kenai Fjords run: 5 photos from what was
# clearly the same camera, all stamped "2000:01:01 00:00:0X", all bucketed
# "night" purely because hour 0 falls there -- the camera's clock was simply
# never configured, not evidence the photos were taken at midnight. See
# DECISIONS.md.
_SENTINEL_DATES = {"2000:01:01"}


_DATETIME_RE = re.compile(r"(\d{4}:\d{2}:\d{2})\s+(\d{2}):\d{2}:\d{2}")


def _date_part(value) -> str | None:
    m = _DATETIME_RE.search(str(value or ""))
    return m.group(1) if m else None


def _parse_hour(value) -> int | None:
    m = _DATETIME_RE.search(str(value or ""))
    return int(m.group(2)) if m else None


_FULL_DATETIME_RE = re.compile(r"(\d{4}):(\d{2}):(\d{2})\s+(\d{2}):(\d{2}):(\d{2})")


def _parse_datetime(value) -> datetime | None:
    m = _FULL_DATETIME_RE.search(str(value or ""))
    if not m:
        return None
    year, month, day, hour, minute, second = (int(g) for g in m.groups())
    try:
        return datetime(year, month, day, hour, minute, second)
    except ValueError:
        return None


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
    for a genuinely morning photo. See DECISIONS.md.

    If DateTimeOriginal/DateTimeDigitized is present but is a known
    clock-never-set sentinel (_SENTINEL_DATES), the IFD0 DateTime fallback
    is skipped too rather than tried -- a sentinel there is a strong signal
    the same camera's other timestamps are equally untrustworthy. Found
    live in the same checkpoint's Kenai Fjords batch: 5 photos stamped
    "2000:01:01 00:00:0X" (hour 0 -> "night") also had an IFD0 DateTime of
    "2023:02:17 21:07:57" -- a plausible-looking but almost certainly
    file-touched (upload/processing) date, not capture time, which
    would have produced a second wrong "night" bucket if used."""
    try:
        with Image.open(path) as img:
            exif = img.getexif()
            if not exif:
                return None
            sub_ifd = exif.get_ifd(ExifTags.IFD.Exif)
    except Exception:
        return None

    sentinel_seen = False
    for tag_name in ("DateTimeOriginal", "DateTimeDigitized"):
        tag_id = next((k for k, v in ExifTags.TAGS.items() if v == tag_name), None)
        raw = sub_ifd.get(tag_id) if tag_id is not None else None
        if raw is None:
            continue
        if _date_part(raw) in _SENTINEL_DATES:
            sentinel_seen = True
            continue
        hour = _parse_hour(raw)
        if hour is not None:
            return hour

    if sentinel_seen:
        return None

    return _parse_hour(exif.get(306))  # IFD0 DateTime -- last resort only


def exif_capture_datetime(path: Path) -> datetime | None:
    """Same evidence priority and sentinel-rejection as exif_capture_hour(),
    but returns the full minute-precision timestamp instead of just an
    hour bucket -- needed for burst-detection (same park, captured within
    minutes of each other), where hour-granularity is too coarse."""
    try:
        with Image.open(path) as img:
            exif = img.getexif()
            if not exif:
                return None
            sub_ifd = exif.get_ifd(ExifTags.IFD.Exif)
    except Exception:
        return None

    sentinel_seen = False
    for tag_name in ("DateTimeOriginal", "DateTimeDigitized"):
        tag_id = next((k for k, v in ExifTags.TAGS.items() if v == tag_name), None)
        raw = sub_ifd.get(tag_id) if tag_id is not None else None
        if raw is None:
            continue
        if _date_part(raw) in _SENTINEL_DATES:
            sentinel_seen = True
            continue
        dt = _parse_datetime(raw)
        if dt is not None:
            return dt

    if sentinel_seen:
        return None

    return _parse_datetime(exif.get(306))  # IFD0 DateTime -- last resort only
