"""Renders the static GitHub Pages site: thumbnails + a filterable JSON index.

Reads data/catalog.json (produced by pipeline.py) and data/images/ (the
downloaded originals), and writes docs/data.json plus docs/thumbs/*.webp.
Nothing here calls the model or the network -- pure deterministic
post-processing of an already-built catalog, same as crop.py.

Only primary_subject == "landscape" records are published -- see
DECISIONS.md, 2026-08-30, "site-inclusion policy" -- and, as of
2026-09-02, only ones scoring >= PUBLISH_MIN_AESTHETIC_SCORE, and, as of
2026-09-05, only ones not listed in hidden_ids.json (manual dedup-review
hides, see dedup_review_server.py), and, as of 2026-09-06, only ones
with content_visible != false (model field -- blank/washed-out/
degraded scans, see schema.json; missing on old records defaults to
true, since this is a future-scrapes-only gate, not backfilled onto
the existing corpus), and, also as of 2026-09-06, only ones
nps_client.is_open_license() accepts (Constraints Information's prefix
is "Public domain" -- see that function's docstring and DECISIONS.md).
This licensing gate was briefly replaced with a site-side filter
earlier the same day, then reverted back to a hard exclusion later
that same day per Josh's explicit call -- see DECISIONS.md for both
entries. Also as of 2026-09-06: only records with license_confidence
== "confirmed", or whose id is in confirmed_ids.json, publish --
"flagged_for_review" is otherwise held off the site entirely until a
human moves it to hidden_ids.json (exclude) or confirmed_ids.json
(include despite the flag) via license_review_server.py's Confirm/Hide
actions (previously nothing gated on this at all, letting 359
unreviewed flagged records go live). confirmed_ids.json exists because
license_confidence itself lives in data/catalog.json on wopr, not
something this repo's tooling can edit in place from a local review
session -- same override-file pattern as hidden_ids.json, opposite
polarity. And minor_face_present == true is excluded
unconditionally, with no pending-review step -- see that field's
schema.json docstring; this is the one gate here that is NOT "just a
display gate, reappears once un-hidden" in spirit, even though
mechanically it's still just a filter over data/catalog.json. All
other gates are ordinary display gates, not deletions: everything
stays in data/catalog.json regardless, and a record reappears here
automatically once it clears whatever the current bar is / is
un-hidden.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

from PIL import Image

from vistarium.crop import crop_9x16, crop_16x9
from vistarium.nps_client import is_open_license

THUMB_WIDTH = 1200
WEBP_QUALITY = 82
# A source original whose long edge is below this isn't wallpaper-sized --
# found live 2026-09-05 (edc77846..., "Flowering Coreopsis": 499x400
# actual Original, nowhere near desktop-wallpaper resolution) despite
# passing every other gate. 1920 is the common Full HD long edge -- below
# it, both our own thumbnail render (which would have to upscale) and a
# user's full-resolution download via image_url/source_url are too small
# to be useful as a wallpaper. Same display-gate-not-deletion treatment:
# nothing is deleted from data/catalog.json, and pipeline.py separately
# gates this at scrape time (before the VLM ever sees it) going forward --
# see DECISIONS.md, 2026-09-05.
MIN_WALLPAPER_LONG_EDGE = 1920
# Display-only gate, not a deletion -- records scoring below this stay in
# data/catalog.json untouched and will reappear here automatically if the
# threshold is lowered later or the record gets rescored. Matches the
# curated scrape's own selection threshold (see
# scripts/run_curated_scrape_remote.py); records without a score at all
# (e.g. any bug that skips scoring) are treated as not meeting it, since
# there's nothing to verify against. See DECISIONS.md, 2026-09-02.
PUBLISH_MIN_AESTHETIC_SCORE = 5.4

# select_by_threshold_with_floor() deliberately tops a park up to
# THIN_PARK_FLOOR candidates even below PUBLISH_MIN_AESTHETIC_SCORE, but
# that guarantee was never reaching the site -- this filter reapplied the
# same 5.4 cut with no memory of which candidates were floor-exceptions,
# silently stripping them back out. Fix: per park, if the standard cut
# leaves it under floor, relax that park's cut to THIN_PARK_RELAXED_SCORE
# instead -- only for that park, only if it's still short. A park still
# under floor even at the relaxed score is genuinely thin (usually a
# subject-matter mismatch, e.g. a cave or fort park under a landscape-only
# policy) and is left as-is, not forced further. See DECISIONS.md, 2026-09-05.
THIN_PARK_FLOOR = 10
THIN_PARK_RELAXED_SCORE = 5.2

# Reverted 2026-09-05: a brief per-park structure allowlist was tried
# for genuinely-thin parks (Gateway Arch, Mesa Verde, Dry Tortugas,
# Virgin Islands) as part of the same remediation that added
# THIN_PARK_RELAXED_SCORE above. Josh's call after seeing it live: these
# were beautiful photos but didn't fit the site's stated goal ("open-
# access landscape photography") or its visual consistency, and
# hand-picking per-park exceptions when a park runs short undermines
# trusting primary_subject's classification uniformly. Structure is back
# to being excluded everywhere, no exceptions -- a thin park now only
# gets the relaxed-score/more-albums treatment below, never a subject
# carve-out. See DECISIONS.md, 2026-09-05 (reversal entry). The better
# fix for "this structure shot is actually great" is a
# structure_present/structure_prominence field on the record itself
# (roadmap), not a site-filter exception.


def _date_sortable(date_str: str | None) -> str | None:
    """Normalizes the deterministic MM/DD/YYYY date field (from NPS source
    metadata) to an ISO string the site can sort on client-side, or None
    if absent/unparseable -- about 65% of records have no source date at
    all (common among archival scans), so callers must handle None by
    sorting those last, not by crashing."""
    if not date_str:
        return None
    try:
        return datetime.strptime(date_str, "%m/%d/%Y").date().isoformat()
    except ValueError:
        return None


def _thumbnail(
    src_path: Path, dest_path: Path, anchor: str, min_long_edge: int = MIN_WALLPAPER_LONG_EDGE
) -> str | None:
    """Renders the thumbnail and returns its aspect ratio as "16/9" or
    "9/16" -- portrait originals get a portrait thumbnail instead of a
    forced 16:9 crop, which can throw away most of the frame (see
    DECISIONS.md, 2026-08-31). The caller needs the ratio to size the
    gallery tile correctly.

    Returns None (and writes nothing) if the source's long edge is below
    min_long_edge -- see MIN_WALLPAPER_LONG_EDGE's docstring."""
    with Image.open(src_path) as im:
        im = im.convert("RGB")
        if max(im.width, im.height) < min_long_edge:
            return None
        portrait = im.height > im.width
        if portrait:
            box = crop_9x16(im.width, im.height, anchor)
            thumb_w = round(THUMB_WIDTH * 9 / 16)
            thumb_h = THUMB_WIDTH
        else:
            box = crop_16x9(im.width, im.height, anchor)
            thumb_w = THUMB_WIDTH
            thumb_h = round(THUMB_WIDTH * 9 / 16)
        cropped = im.crop((box["x"], box["y"], box["x"] + box["w"], box["y"] + box["h"]))
        cropped = cropped.resize((thumb_w, thumb_h), Image.LANCZOS)
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        cropped.save(dest_path, "WEBP", quality=WEBP_QUALITY)
        return "9/16" if portrait else "16/9"


def _is_360_panorama(title: str) -> bool:
    """NPS titles genuine equirectangular 360-degree panoramas explicitly
    ("360 photo", "360 image", "360 degrees", "360 Panorama") -- found
    live 2026-09-01 scraping Acadia's albums. No crop_anchor/crop box can
    fix the inherent barrel distortion of an equirectangular projection
    (visible curved horizon, warped foreground, tripod/camera often in
    frame); these can't be made into a good wallpaper by cropping, unlike
    genuine wide-format panoramic photography (which isn't titled this
    way and crops fine). See DECISIONS.md."""
    return "360" in title


def _load_id_set(path: Path) -> set[str]:
    """Shared loader for the small, hand-curated id-list override files at
    the repo root (hidden_ids.json, confirmed_ids.json) -- real curation
    decisions, not regeneratable scrape output. Missing file means no
    decisions recorded yet, not an error (a fresh checkout, or wopr
    before the file's been synced there)."""
    if not path.exists():
        return set()
    return set(json.loads(path.read_text()))


def build_site(
    catalog_path: Path,
    images_dir: Path,
    out_dir: Path,
    thumbs_dirname: str = "thumbs",
    hidden_ids_path: Path = Path("hidden_ids.json"),
    min_long_edge: int = MIN_WALLPAPER_LONG_EDGE,
    confirmed_ids_path: Path = Path("confirmed_ids.json"),
) -> int:
    catalog = json.loads(catalog_path.read_text())
    hidden_ids = _load_id_set(hidden_ids_path)
    confirmed_ids = _load_id_set(confirmed_ids_path)
    eligible = [
        r
        for r in catalog
        if r.get("primary_subject") == "landscape"
        and not _is_360_panorama(r.get("title", ""))
        and r.get("aesthetic_score") is not None
        and r["id"] not in hidden_ids
        and r.get("content_visible", True)
        and is_open_license(r.get("license", ""))
        and (r.get("license_confidence") == "confirmed" or r["id"] in confirmed_ids)
        and not r.get("minor_face_present", False)
    ]
    landscape = [r for r in eligible if r["aesthetic_score"] >= PUBLISH_MIN_AESTHETIC_SCORE]

    by_park: dict[str, int] = {}
    for r in landscape:
        by_park[r["park"]] = by_park.get(r["park"], 0) + 1
    thin_parks = {park for park, count in by_park.items() if count < THIN_PARK_FLOOR}
    if thin_parks:
        published_ids = {r["id"] for r in landscape}
        landscape += [
            r
            for r in eligible
            if r["park"] in thin_parks
            and r["id"] not in published_ids
            and r["aesthetic_score"] >= THIN_PARK_RELAXED_SCORE
        ]

    thumbs_dir = out_dir / thumbs_dirname
    index: list[dict] = []
    for record in landscape:
        src_path = images_dir / f"{record['id']}.jpg"
        if not src_path.exists():
            continue
        thumb_name = f"{record['id']}.webp"
        aspect = _thumbnail(src_path, thumbs_dir / thumb_name, record["crop_anchor"], min_long_edge)
        if aspect is None:
            continue
        index.append(
            {
                "id": record["id"],
                "title": record["title"],
                "photographer": record["photographer"],
                "date": record["date"],
                "park": record["park"],
                "license": record["license"],
                "license_confidence": record["license_confidence"],
                "source_url": record["source_url"],
                "image_url": record["image_url"],
                "time_of_day": record["time_of_day"],
                "people_prominence": record["people_prominence"],
                "color_mode": record["color_mode"],
                "dominant_color": record["dominant_color"],
                "tags": record["tags"],
                "thumb": f"{thumbs_dirname}/{thumb_name}",
                "aspect": aspect,
                "aesthetic_score": record.get("aesthetic_score"),
                "date_sortable": _date_sortable(record["date"]),
            }
        )

    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "data.json").write_text(json.dumps(index, indent=2))
    return len(index)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", type=Path, default=Path("data/catalog.json"))
    parser.add_argument("--images", type=Path, default=Path("data/images"))
    parser.add_argument("--out", type=Path, default=Path("docs"))
    parser.add_argument("--hidden-ids", type=Path, default=Path("hidden_ids.json"))
    parser.add_argument("--min-long-edge", type=int, default=MIN_WALLPAPER_LONG_EDGE)
    parser.add_argument("--confirmed-ids", type=Path, default=Path("confirmed_ids.json"))
    args = parser.parse_args()

    count = build_site(
        args.catalog,
        args.images,
        args.out,
        hidden_ids_path=args.hidden_ids,
        min_long_edge=args.min_long_edge,
        confirmed_ids_path=args.confirmed_ids,
    )
    print(f"wrote {count} records to {args.out / 'data.json'}")
    print(f"wrote thumbnails to {args.out / 'thumbs'}")


if __name__ == "__main__":
    main()
