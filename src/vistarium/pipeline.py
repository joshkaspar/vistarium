"""End-to-end NPS pipeline: search -> download -> dedup -> judge -> merge -> validate.

Build order step 1 (project-kickoff.md): get the mechanical pipeline and
the model judgment step working end-to-end against NPS only. Step 2 (a
20-50 image validation checkpoint, hand-checked before scaling volume) is
a deliberate separate action -- this script is what that checkpoint runs,
not a replacement for the human review it requires.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import logging
import sys
from pathlib import Path

from dotenv import load_dotenv
from PIL import Image

from vistarium import crop, exif_util, nps_client, schema_validate
from vistarium.dedup import Deduplicator
from vistarium.model_client import ModelJudgmentError, judge_image

log = logging.getLogger("vistarium.pipeline")


def resolve_time_of_day(
    caption_text: str, exif_hour: int | None, model_time_of_day: str
) -> tuple[str, str]:
    """Deterministic time-of-day evidence takes priority over the model's
    visual_inference when available -- see exif_util.py. EXIF is checked
    first, ahead of caption text: a real camera timestamp is a hard fact,
    while caption regex matching is a heuristic prone to false positives
    from proper names (found live in this project's own 2026-08-30
    validation checkpoint -- "Dawn Marsh" in a photo credit list matched
    the "dawn" keyword and overrode a correct 11:42 AM EXIF timestamp with
    an incorrect "morning" bucket; see DECISIONS.md). Returns
    (time_of_day, time_of_day_evidence).

    Deliberately does not accept or trust the model's own self-reported
    time_of_day_evidence: the grammar's enum lets it emit "caption" or
    "exif_timestamp" even though it only ever receives pixels, and it did
    so at least once in that same checkpoint run. Claude Code, not the
    model, decides which evidence source was actually used -- when
    falling through to the model's guess, the evidence is unconditionally
    "visual_inference"."""
    if exif_hour is not None:
        return exif_util.hour_to_bucket(exif_hour), "exif_timestamp"
    caption_bucket = exif_util.caption_time_of_day(caption_text)
    if caption_bucket:
        return caption_bucket, "caption"
    return model_time_of_day, "visual_inference"


def build_record(candidate: nps_client.NPSCandidate, image_path: Path) -> dict | None:
    """Returns a schema-valid record, or None if the model never returned
    usable JSON for this image (logged, not raised -- one bad image
    shouldn't abort a batch run)."""
    try:
        model_fields = judge_image(image_path)
    except ModelJudgmentError as e:
        log.warning("skipping %s: %s", candidate.id, e)
        return None

    exif_hour = exif_util.exif_capture_hour(image_path)
    model_fields["time_of_day"], model_fields["time_of_day_evidence"] = resolve_time_of_day(
        candidate.caption_text,
        exif_hour,
        model_fields["time_of_day"],
    )

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


def _load_checkpoint(checkpoint_path: Path) -> tuple[dict[str, dict], set[str]]:
    """Returns (outcomes_by_id, processed_ids) from a prior run's checkpoint,
    or ({}, set()) if none exists. `outcomes_by_id` maps candidate ID to the
    checkpoint line (with "id", "outcome", and "record" if outcome is
    "catalog" or "excluded") -- this is what lets a resumed run rebuild the
    full catalog across interruptions instead of only this invocation's
    work."""
    outcomes: dict[str, dict] = {}
    if checkpoint_path.exists():
        for line in checkpoint_path.read_text().splitlines():
            if not line.strip():
                continue
            entry = json.loads(line)
            outcomes[entry["id"]] = entry
    return outcomes, set(outcomes.keys())


def _write_checkpoint_line(checkpoint_path: Path, entry: dict) -> None:
    with open(checkpoint_path, "a") as f:
        f.write(json.dumps(entry) + "\n")
        f.flush()


def _search_with_cache(
    cache_path: Path, terms: list[str] | None, refresh: bool
) -> list[nps_client.NPSCandidate]:
    """NPS search across ~28 terms takes 90+ seconds -- most of a short
    run's time budget if it has to be redone on every retry. Cache the
    results to disk; pass refresh=True (--refresh-search) to force a
    re-scan (e.g. to pick up newly-added NPS assets)."""
    if cache_path.exists() and not refresh:
        raw = json.loads(cache_path.read_text())
        return [nps_client.NPSCandidate(**c) for c in raw]

    candidates = nps_client.search_candidates(terms=terms)
    cache_path.write_text(json.dumps([dataclasses.asdict(c) for c in candidates]))
    return candidates


def _filter_by_park(
    candidates: list[nps_client.NPSCandidate], park: str | None
) -> list[nps_client.NPSCandidate]:
    """Case-insensitive substring match against each candidate's park field.
    Returns `candidates` unchanged if `park` is None/empty."""
    if not park:
        return candidates
    needle = park.lower()
    return [c for c in candidates if needle in c.park.lower()]


def run(
    *,
    limit: int,
    workdir: Path,
    out_path: Path,
    excluded_out_path: Path,
    terms: list[str] | None,
    refresh_search: bool = False,
    park: str | None = None,
) -> None:
    images_dir = workdir / "images"
    checkpoint_path = workdir / "checkpoint.jsonl"
    candidates_cache_path = workdir / "candidates_cache.json"
    workdir.mkdir(parents=True, exist_ok=True)

    outcomes, already_processed = _load_checkpoint(checkpoint_path)
    if already_processed:
        log.info("resuming: %d candidates already processed in a prior run", len(already_processed))

    log.info("searching NPS (cached unless --refresh-search)...")
    candidates = _search_with_cache(candidates_cache_path, terms, refresh_search)
    log.info("found %d unique candidates", len(candidates))
    if park:
        candidates = _filter_by_park(candidates, park)
        log.info("filtered to %d candidates matching park %r", len(candidates), park)
    new_candidates = [c for c in candidates if c.id not in already_processed][:limit]
    log.info("processing %d new candidates this run", len(new_candidates))

    dedup = Deduplicator()
    # Pre-seed dedup with already-downloaded images so a resumed run still
    # catches duplicates against work done in a prior invocation.
    if images_dir.exists():
        for existing in images_dir.glob("*.jpg"):
            dedup.is_duplicate(existing)

    for i, candidate in enumerate(new_candidates, 1):
        log.info("[%d/%d] %s: %s", i, len(new_candidates), candidate.id, candidate.title[:60])
        try:
            image_path = nps_client.download_image(candidate, images_dir)
        except Exception as e:
            log.warning("download failed for %s: %s", candidate.id, e)
            _write_checkpoint_line(
                checkpoint_path, {"id": candidate.id, "outcome": "download_failed"}
            )
            continue

        dup_of = dedup.is_duplicate(image_path)
        if dup_of is not None:
            log.info("  duplicate of %s, skipping", dup_of.name)
            _write_checkpoint_line(checkpoint_path, {"id": candidate.id, "outcome": "duplicate"})
            continue

        record = build_record(candidate, image_path)
        if record is None:
            _write_checkpoint_line(
                checkpoint_path, {"id": candidate.id, "outcome": "no_model_json"}
            )
            continue

        try:
            schema_validate.validate_record(record)
        except Exception as e:
            log.error("  schema validation failed for %s: %s", candidate.id, e)
            _write_checkpoint_line(
                checkpoint_path, {"id": candidate.id, "outcome": "invalid_schema"}
            )
            continue

        outcome = "excluded" if record["is_photograph"] is False else "catalog"
        if outcome == "excluded":
            log.info("  not a photograph, routing to excluded set")
        entry = {"id": candidate.id, "outcome": outcome, "record": record}
        _write_checkpoint_line(checkpoint_path, entry)
        outcomes[candidate.id] = entry

    records = [o["record"] for o in outcomes.values() if o["outcome"] == "catalog"]
    excluded = [o["record"] for o in outcomes.values() if o["outcome"] == "excluded"]

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
    load_dotenv()  # WOPR_BASE_URL and any future secrets come from .env, not hardcoded values
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
    parser.add_argument(
        "--refresh-search",
        action="store_true",
        help="re-scan NPS instead of using the cached candidate list from a prior run",
    )
    parser.add_argument(
        "--park",
        help="restrict to candidates whose park field contains this (case-insensitive substring)",
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
        refresh_search=args.refresh_search,
        park=args.park,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
