"""End-to-end NPS pipeline: search -> download -> dedup -> judge -> merge -> validate.

Build order step 1 (project-kickoff.md): get the mechanical pipeline and
the model judgment step working end-to-end against NPS only. Step 2 (a
20-50 image validation checkpoint, hand-checked before scaling volume) is
a deliberate separate action -- this script is what that checkpoint runs,
not a replacement for the human review it requires.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from PIL import Image

from vistarium import crop, exif_util, nps_client, schema_validate
from vistarium.dedup import Deduplicator
from vistarium.model_client import ModelJudgmentError, judge_image

log = logging.getLogger("vistarium.pipeline")


def build_record(candidate: nps_client.NPSCandidate, image_path: Path) -> dict | None:
    """Returns a schema-valid record, or None if the model never returned
    usable JSON for this image (logged, not raised -- one bad image
    shouldn't abort a batch run)."""
    try:
        model_fields = judge_image(image_path)
    except ModelJudgmentError as e:
        log.warning("skipping %s: %s", candidate.id, e)
        return None

    # Deterministic time-of-day evidence takes priority over the model's
    # visual_inference when available with confidence -- see exif_util.py.
    caption_bucket = exif_util.caption_time_of_day(candidate.caption_text)
    exif_hour = exif_util.exif_capture_hour(image_path)
    if caption_bucket:
        model_fields["time_of_day"] = caption_bucket
        model_fields["time_of_day_evidence"] = "caption"
    elif exif_hour is not None:
        model_fields["time_of_day"] = exif_util.hour_to_bucket(exif_hour)
        model_fields["time_of_day_evidence"] = "exif_timestamp"
    # else: keep whatever the model returned (visual_inference).

    with Image.open(image_path) as img:
        img_w, img_h = img.size
    thumbnail_crop = crop.crop_16x9(img_w, img_h, model_fields["crop_anchor"])

    record = {
        "id": candidate.id,
        "source": candidate.source,
        "source_url": candidate.source_url,
        "image_url": candidate.image_url,
        "title": candidate.title,
        "photographer": candidate.photographer,
        "date": candidate.date,
        "park": candidate.park,
        "license": candidate.license,
        "thumbnail_crop_16x9": thumbnail_crop,
        **model_fields,
    }
    return record


def run(
    *,
    limit: int,
    workdir: Path,
    out_path: Path,
    excluded_out_path: Path,
    terms: list[str] | None,
) -> None:
    images_dir = workdir / "images"
    log.info("searching NPS...")
    candidates = nps_client.search_candidates(terms=terms)
    log.info("found %d unique candidates", len(candidates))
    candidates = candidates[:limit]

    dedup = Deduplicator()
    records: list[dict] = []
    excluded: list[dict] = []

    for i, candidate in enumerate(candidates, 1):
        log.info("[%d/%d] %s: %s", i, len(candidates), candidate.id, candidate.title[:60])
        try:
            image_path = nps_client.download_image(candidate, images_dir)
        except Exception as e:
            log.warning("download failed for %s: %s", candidate.id, e)
            continue

        dup_of = dedup.is_duplicate(image_path)
        if dup_of is not None:
            log.info("  duplicate of %s, skipping", dup_of.name)
            continue

        record = build_record(candidate, image_path)
        if record is None:
            continue

        try:
            schema_validate.validate_record(record)
        except Exception as e:
            log.error("  schema validation failed for %s: %s", candidate.id, e)
            continue

        if record["is_photograph"] is False:
            log.info("  not a photograph, routing to excluded set")
            excluded.append(record)
        else:
            records.append(record)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(records, indent=2))
    excluded_out_path.write_text(json.dumps(excluded, indent=2))
    log.info(
        "DONE: %d catalog records -> %s (%d excluded non-photographs -> %s)",
        len(records),
        out_path,
        len(excluded),
        excluded_out_path,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--limit",
        type=int,
        default=20,
        help="max candidates to process (default 20, for the validation checkpoint)",
    )
    parser.add_argument(
        "--workdir", type=Path, default=Path("data"), help="scratch dir for downloaded images"
    )
    parser.add_argument("--out", type=Path, default=Path("data/catalog.json"))
    parser.add_argument("--excluded-out", type=Path, default=Path("data/excluded_non_photo.json"))
    parser.add_argument(
        "--term",
        action="append",
        dest="terms",
        help="restrict search to specific term(s); repeatable",
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    run(
        limit=args.limit,
        workdir=args.workdir,
        out_path=args.out,
        excluded_out_path=args.excluded_out,
        terms=args.terms,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
