"""Renders the static GitHub Pages site: thumbnails + a filterable JSON index.

Reads data/catalog.json (produced by pipeline.py) and data/images/ (the
downloaded originals), and writes docs/data.json plus docs/thumbs/*.webp.
Nothing here calls the model or the network -- pure deterministic
post-processing of an already-built catalog, same as crop.py.

Only primary_subject == "landscape" records are published -- see
DECISIONS.md, 2026-08-30, "site-inclusion policy".
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from PIL import Image

from vistarium.crop import crop_9x16, crop_16x9

THUMB_WIDTH = 1200
WEBP_QUALITY = 82


def _thumbnail(src_path: Path, dest_path: Path, anchor: str) -> str:
    """Renders the thumbnail and returns its aspect ratio as "16/9" or
    "9/16" -- portrait originals get a portrait thumbnail instead of a
    forced 16:9 crop, which can throw away most of the frame (see
    DECISIONS.md, 2026-08-31). The caller needs the ratio to size the
    gallery tile correctly."""
    with Image.open(src_path) as im:
        im = im.convert("RGB")
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


def build_site(
    catalog_path: Path, images_dir: Path, out_dir: Path, thumbs_dirname: str = "thumbs"
) -> int:
    catalog = json.loads(catalog_path.read_text())
    landscape = [r for r in catalog if r.get("primary_subject") == "landscape"]

    thumbs_dir = out_dir / thumbs_dirname
    index: list[dict] = []
    for record in landscape:
        src_path = images_dir / f"{record['id']}.jpg"
        if not src_path.exists():
            continue
        thumb_name = f"{record['id']}.webp"
        aspect = _thumbnail(src_path, thumbs_dir / thumb_name, record["crop_anchor"])
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
                "time_of_day": record["time_of_day"],
                "people_prominence": record["people_prominence"],
                "color_mode": record["color_mode"],
                "tags": record["tags"],
                "thumb": f"{thumbs_dirname}/{thumb_name}",
                "aspect": aspect,
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
    args = parser.parse_args()

    count = build_site(args.catalog, args.images, args.out)
    print(f"wrote {count} records to {args.out / 'data.json'}")
    print(f"wrote thumbnails to {args.out / 'thumbs'}")


if __name__ == "__main__":
    main()
