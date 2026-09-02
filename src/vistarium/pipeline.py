"""End-to-end NPS pipeline: search -> download -> dedup -> judge -> merge -> validate.

Gets the mechanical pipeline and the model judgment step working
end-to-end against NPS only. A validation checkpoint (a small batch,
hand-checked before scaling volume) is a deliberate separate action --
this script is what that checkpoint runs, not a replacement for the
human review it requires.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import logging
import random
import sys
from pathlib import Path

from dotenv import load_dotenv
from PIL import Image

from vistarium import album_triage, crop, curate, exif_util, nps_client, schema_validate
from vistarium.dedup import Deduplicator
from vistarium.model_client import ModelJudgmentError, judge_image

log = logging.getLogger("vistarium.pipeline")


def resolve_time_of_day(
    caption_text: str, exif_hour: int | None, model_time_of_day: str, frame_type: str = "full_bleed"
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

    `frame_type != "full_bleed"` (matted/multi_panel/stereograph) means
    this is a scan of a physical print/negative, not a native digital
    photo -- its EXIF DateTimeOriginal is when the print was *scanned*,
    not when the photo was *taken*, and is not trustworthy as a capture
    timestamp at all (found live 2026-08-31: a 1937 Yosemite negative's
    scan EXIF read "01:10 AM," bucketing an obviously bright daytime
    photo to "night"). EXIF is skipped entirely for these, falling
    through to caption/visual_inference same as if no EXIF existed.

    Deliberately does not accept or trust the model's own self-reported
    time_of_day_evidence: the grammar's enum lets it emit "caption" or
    "exif_timestamp" even though it only ever receives pixels, and it did
    so at least once in that same checkpoint run. Claude Code, not the
    model, decides which evidence source was actually used -- when
    falling through to the model's guess, the evidence is unconditionally
    "visual_inference"."""
    if exif_hour is not None and frame_type == "full_bleed":
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
        model_fields["frame_type"],
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
    cache_path: Path,
    terms: list[str] | None,
    refresh: bool,
    park_code: str | None = None,
    album_ids: list[str] | None = None,
    curate_park_code: str | None = None,
    threshold: float | None = None,
    floor: int = 10,
    keywords_path: Path = album_triage.DEFAULT_KEYWORDS_PATH,
    workdir: Path | None = None,
) -> list[nps_client.NPSCandidate]:
    """NPS search takes real time (a park's Categories:Scenic search alone
    can be several requests) -- most of a short run's time budget if it
    has to be redone on every retry. Cache the results to disk; pass
    refresh=True (--refresh-search) to force a re-scan (e.g. to pick up
    newly-added NPS assets).

    Precedence, highest first: curate_park_code (--curate-park-code) runs
    the full curate.select_candidates_for_park() pipeline -- album-keyword
    triage, thumbnail fetch, aesthetic pre-scoring, threshold-with-floor
    selection -- the strategy for scraping at real NPGallery scale (a
    single Categories:Scenic search can return hundreds of thousands of
    candidates NPS-wide; see DECISIONS.md, 2026-09-01). Falls back to
    album_ids (--album-id, repeatable): one or more hand-curated albums
    via nps_client.search_album(), fetched in full with no pre-filtering
    -- appropriate for a small, already-vetted album list, not real
    scale. Falls back to park_code (--park-code),
    nps_client.search_park_scenic()'s Categories:Scenic tag scoped to a
    park -- a smoke-test/volume strategy, weaker than curation but far
    better than guessed keywords. Falls back to terms (DEFAULT_TERMS)
    last. See nps_client.py's module docstring."""
    if cache_path.exists() and not refresh:
        raw = json.loads(cache_path.read_text())
        return [nps_client.NPSCandidate(**c) for c in raw]

    if curate_park_code:
        assert threshold is not None, "--threshold is required with --curate-park-code"
        assert workdir is not None
        candidates = curate.select_candidates_for_park(
            curate_park_code, workdir, threshold, floor, keywords_path
        )
    elif album_ids:
        by_id: dict[str, nps_client.NPSCandidate] = {}
        for album_id in album_ids:
            for cand in nps_client.search_album(album_id, park_code=park_code):
                by_id.setdefault(cand.id, cand)
        candidates = list(by_id.values())
    elif park_code:
        candidates = nps_client.search_park_scenic(park_code)
    else:
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


def _sample_candidates(
    candidates: list[nps_client.NPSCandidate],
    already_processed: set[str],
    limit: int,
    preserve_order: bool = False,
) -> list[nps_client.NPSCandidate]:
    """Selects up to `limit` not-yet-processed candidates.

    Randomly samples by default -- NPS's own default result ordering is
    not random, and a park's real candidate pool can be huge (15,242
    for Kenai Fjords alone via Categories:Scenic, see DECISIONS.md
    2026-09-01), so taking the first N would silently bias every run
    toward whatever NPS happens to sort first rather than a
    representative slice of the park's actual photography.

    `preserve_order=True` (used for the curated path) instead takes a
    positional prefix. There the input order is curate.py's
    score-descending sort, which is meaningful and deliberate -- Josh
    wants VLM tagging to hit the best-scoring candidates first, so
    shuffling it would defeat the point (see DECISIONS.md,
    2026-09-01)."""
    unprocessed = [c for c in candidates if c.id not in already_processed]
    if len(unprocessed) <= limit or preserve_order:
        return unprocessed[:limit]
    return random.sample(unprocessed, limit)


def run(
    *,
    limit: int,
    workdir: Path,
    out_path: Path,
    excluded_out_path: Path,
    terms: list[str] | None,
    refresh_search: bool = False,
    park: str | None = None,
    park_code: str | None = None,
    album_ids: list[str] | None = None,
    curate_park_code: str | None = None,
    threshold: float | None = None,
    floor: int = 10,
    keywords_path: Path = album_triage.DEFAULT_KEYWORDS_PATH,
) -> None:
    images_dir = workdir / "images"
    checkpoint_path = workdir / "checkpoint.jsonl"
    candidates_cache_path = workdir / "candidates_cache.json"
    workdir.mkdir(parents=True, exist_ok=True)

    outcomes, already_processed = _load_checkpoint(checkpoint_path)
    if already_processed:
        log.info("resuming: %d candidates already processed in a prior run", len(already_processed))

    log.info("searching NPS (cached unless --refresh-search)...")
    candidates = _search_with_cache(
        candidates_cache_path,
        terms,
        refresh_search,
        park_code,
        album_ids,
        curate_park_code,
        threshold,
        floor,
        keywords_path,
        workdir,
    )
    log.info("found %d unique candidates", len(candidates))
    if park:
        candidates = _filter_by_park(candidates, park)
        log.info("filtered to %d candidates matching park %r", len(candidates), park)
    new_candidates = _sample_candidates(
        candidates, already_processed, limit, preserve_order=bool(curate_park_code)
    )
    log.info(
        "processing %d new candidates this run (%s)",
        len(new_candidates),
        "score order" if curate_park_code else "random sample",
    )

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
        if dup_of is not None and dup_of != image_path:
            # dup_of == image_path means this candidate's own file was
            # pre-seeded from a prior run that crashed after download but
            # before its checkpoint line was written (e.g. a judge_image
            # failure -- see DECISIONS.md, 2026-09-02) -- not a real
            # duplicate, just this candidate meeting its own pre-seeded
            # entry for the first time.
            log.info("  duplicate of %s, skipping", dup_of.name)
            _write_checkpoint_line(checkpoint_path, {"id": candidate.id, "outcome": "duplicate"})
            continue

        try:
            record = build_record(candidate, image_path)
        except Exception as e:
            # Broader than ModelJudgmentError on purpose -- e.g. a source
            # file large enough to trip PIL's decompression-bomb guard.
            # One bad image shouldn't abort every remaining candidate in
            # the run; see DECISIONS.md, 2026-08-31.
            log.warning("  unexpected error building record for %s: %s", candidate.id, e)
            _write_checkpoint_line(
                checkpoint_path, {"id": candidate.id, "outcome": "processing_error"}
            )
            continue
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
    parser.add_argument(
        "--park-code",
        help=(
            "4-letter NPS unit code (e.g. ACAD) -- searches that park's own "
            "Categories:Scenic tag directly instead of guessing DEFAULT_TERMS "
            "keywords (see nps_client.search_park_scenic). Overrides --term. "
            "Resolve a code from a park name with nps_client.fetch_unit_codes()."
        ),
    )
    parser.add_argument(
        "--album-id",
        action="append",
        dest="album_ids",
        help=(
            "NPGallery album id (repeatable) -- fetches a hand-curated album's "
            "full contents (see nps_client.search_album/list_park_albums), with "
            "no pre-filtering. Fine for a small, already-vetted list; use "
            "--curate-park-code instead at real scale. Overrides --park-code "
            "and --term."
        ),
    )
    parser.add_argument(
        "--curate-park-code",
        help=(
            "4-letter NPS unit code -- runs the full curated-scale pipeline "
            "(album-keyword triage, thumbnail fetch, aesthetic pre-scoring, "
            "threshold-with-floor selection; see curate.py). Requires "
            "--threshold. Overrides --album-id, --park-code, and --term."
        ),
    )
    parser.add_argument(
        "--threshold",
        type=float,
        help="minimum aesthetic_score to keep a candidate; required with --curate-park-code",
    )
    parser.add_argument(
        "--floor",
        type=int,
        default=10,
        help="min candidates to keep per park even below --threshold (default 10)",
    )
    parser.add_argument(
        "--keywords",
        type=Path,
        default=album_triage.DEFAULT_KEYWORDS_PATH,
        help="path to the album include/exclude keyword config (default album_keywords.json)",
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    if args.curate_park_code and args.threshold is None:
        parser.error("--threshold is required with --curate-park-code")

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
        park_code=args.park_code,
        album_ids=args.album_ids,
        curate_park_code=args.curate_park_code,
        threshold=args.threshold,
        floor=args.floor,
        keywords_path=args.keywords,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
