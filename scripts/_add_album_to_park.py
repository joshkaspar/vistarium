#!/usr/bin/env python3
"""One-off: add a specific album's candidates to an already-scraped park.

Built for the 2026-09-05 underrepresented-park remediation (step 4, see
DECISIONS.md/ROADMAP.md) -- a park's album-keyword triage can throw out
an album for an incidental keyword collision (e.g. Carlsbad Caverns'
"Historic Photograph Collection" excluded only because its description
says "documented by park staff", matching the generic exclude keyword
"staff") even though the album has real scenic content. Rather than
building a generalized re-triage tool, this does exactly what's needed
for one manually-identified album: fetch -> thumbnail -> score -> select
above the given threshold -> full-res download + VLM tag -> append to
catalog.json + checkpoint.jsonl. Mirrors pipeline.run()'s per-candidate
loop (dedup check, schema validation, is_photograph routing) so this
run is indistinguishable from a normal one in the resulting data.

Run on wopr only (needs the VLM/model_client access + full-res storage).

Usage: python scripts/_add_album_to_park.py <album_id> <park_code> <threshold>
"""

import json
import logging
import sys
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, "src")

from dotenv import load_dotenv  # noqa: E402

from vistarium import aesthetic_score, nps_client, pipeline, schema_validate  # noqa: E402
from vistarium.dedup import Deduplicator  # noqa: E402

load_dotenv(
    Path(__file__).resolve().parent.parent / ".env"
)  # WOPR_BASE_URL -- judge_image() needs this

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("add_album")

WORKDIR = Path("data")
CATALOG_PATH = WORKDIR / "catalog.json"
EXCLUDED_PATH = WORKDIR / "excluded_non_photo.json"
CHECKPOINT_PATH = WORKDIR / "checkpoint.jsonl"
IMAGES_DIR = WORKDIR / "images"


def _checkpoint_line(entry: dict) -> None:
    with CHECKPOINT_PATH.open("a") as f:
        f.write(json.dumps(entry) + "\n")
        f.flush()


def main() -> None:
    album_id, park_code, threshold_s = sys.argv[1], sys.argv[2], sys.argv[3]
    threshold = float(threshold_s)

    already_done = set()
    if CHECKPOINT_PATH.exists():
        with CHECKPOINT_PATH.open() as f:
            for line in f:
                already_done.add(json.loads(line)["id"])

    candidates = nps_client.search_album(album_id, park_code=park_code)
    candidates = [c for c in candidates if c.id not in already_done]
    log.info("%d new candidates in album (already-processed ones skipped)", len(candidates))

    thumbs_dir = WORKDIR / "thumbs_cache"
    paths = []
    for c in candidates:
        try:
            paths.append(nps_client.download_thumbnail(c, thumbs_dir))
        except Exception as e:
            log.warning("thumbnail fetch failed for %s: %s", c.id, e)

    scores = aesthetic_score.score_all(paths)
    selected = [c for c in candidates if scores.get(c.id, 0) >= threshold]
    log.info("%d/%d candidates clear threshold=%.2f", len(selected), len(candidates), threshold)

    dedup = Deduplicator()
    for existing in IMAGES_DIR.glob("*.jpg"):
        dedup.is_duplicate(existing)

    catalog = json.loads(CATALOG_PATH.read_text()) if CATALOG_PATH.exists() else []
    excluded = json.loads(EXCLUDED_PATH.read_text()) if EXCLUDED_PATH.exists() else []
    added = 0
    for c in selected:
        c = replace(
            c, aesthetic_score=scores[c.id], aesthetic_method=aesthetic_score.AESTHETIC_METHOD
        )

        try:
            image_path = nps_client.download_image(c, IMAGES_DIR)
        except Exception as e:
            log.warning("download failed for %s: %s", c.id, e)
            _checkpoint_line({"id": c.id, "outcome": "download_failed"})
            continue

        dup_of = dedup.is_duplicate(image_path)
        if dup_of is not None and dup_of != image_path:
            log.info("  %s duplicate of %s, skipping", c.id, dup_of.name)
            _checkpoint_line({"id": c.id, "outcome": "duplicate"})
            continue

        try:
            record = pipeline.build_record(c, image_path)
        except Exception as e:
            log.warning("  unexpected error building record for %s: %s", c.id, e)
            _checkpoint_line({"id": c.id, "outcome": "processing_error"})
            continue
        if record is None:
            _checkpoint_line({"id": c.id, "outcome": "no_model_json"})
            continue

        try:
            schema_validate.validate_record(record)
        except Exception as e:
            log.error("  schema validation failed for %s: %s", c.id, e)
            _checkpoint_line({"id": c.id, "outcome": "invalid_schema"})
            continue

        if record["is_photograph"] is False:
            excluded.append(record)
            _checkpoint_line({"id": c.id, "outcome": "excluded"})
            log.info("  %s not a photograph, routed to excluded set", c.id)
            continue

        catalog.append(record)
        _checkpoint_line({"id": c.id, "outcome": "catalog"})
        added += 1
        log.info("  added %s (%s): %s", c.id, record.get("primary_subject"), record.get("title"))

    CATALOG_PATH.write_text(json.dumps(catalog, indent=2))
    EXCLUDED_PATH.write_text(json.dumps(excluded, indent=2))
    log.info("done -- %d records added, catalog now %d total", added, len(catalog))


if __name__ == "__main__":
    main()
