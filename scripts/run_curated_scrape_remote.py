"""Runs the curated-scale pipeline (see curate.py, DECISIONS.md
2026-09-01) across every official U.S. National Park. Meant to run on
wopr (GPU host with torch installed -- the dev VM has ~1GB free disk
and can't hold torch, see pyproject.toml's `aesthetic` extra comment),
against wopr's own rsynced copy of the repo, so it does no git
operations itself. sync_and_publish.py (run from the actual git
checkout) periodically pulls docs/data.json and docs/thumbs back and
publishes the site.

Producer/consumer, not strictly sequential (see DECISIONS.md,
2026-09-02, ~28hr estimated savings): a producer thread does album
triage + thumbnail fetch + CPU aesthetic scoring + threshold-with-floor
selection, park by park, pushing each park's selected candidates onto
a queue as soon as that park's selection is final -- it does not wait
for tagging. The consumer (this thread) pulls off that queue and does
full-res download + VLM tagging (GPU), decoupled from which park the
producer is currently working on. This overlaps GPU tag time for park
N with network/CPU work for park N+1 onward, instead of paying for
both in sequence.

Aesthetic scoring is forced to CPU (aesthetic_score.set_device("cpu"))
specifically so it never contends with the GPU-resident VLM tagging
model (llama-swap/qwen, ~26.5GB of 32GB used) for VRAM -- a second
CUDA context fighting for the remaining headroom risked eviction/
reload thrashing far more costly than CPU's slower raw throughput.
CPU keeps pace fine here regardless (~1 img/s vs. the NPS throttle's
~0.28 img/s), so it costs nothing once overlapped with fetching.

Not part of the installed package -- a long-running (multi-day, NPS
throttle-bound) driver script:

    ~/aesthetic-pilot/.venv/bin/python scripts/run_curated_scrape_remote.py

Safe to interrupt and re-run: the consumer checkpoints per-candidate
to data/checkpoint.jsonl same as before, and a park is only marked
done in data/curated_scrape_progress.json once the consumer finishes
tagging everything the producer queued for it -- so a restart skips
finished parks and re-queues (but doesn't re-download/re-tag, per the
checkpoint) any partially-worked one.
"""

from __future__ import annotations

import json
import logging
import queue
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from dotenv import load_dotenv  # noqa: E402

from vistarium import aesthetic_score, curate, nps_client, pipeline, schema_validate  # noqa: E402
from vistarium.dedup import Deduplicator  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
PARKS_PATH = REPO_ROOT / "national_parks.json"
PROGRESS_PATH = REPO_ROOT / "data" / "curated_scrape_progress.json"
WORKDIR = REPO_ROOT / "data"
CHECKPOINT_PATH = WORKDIR / "checkpoint.jsonl"
CATALOG_PATH = WORKDIR / "catalog.json"
EXCLUDED_PATH = WORKDIR / "excluded_non_photo.json"
THRESHOLD = 5.4
FLOOR = 10

log = logging.getLogger("vistarium.curated_scrape")

_PARK_DONE = "__park_done__"  # sentinel tag, paired with a park code
_ALL_DONE = None  # sentinel: no more parks will ever be queued


def _load_progress() -> set[str]:
    if PROGRESS_PATH.exists():
        return set(json.loads(PROGRESS_PATH.read_text()))
    return set()


def _mark_done(code: str, done: set[str]) -> None:
    done.add(code)
    PROGRESS_PATH.parent.mkdir(parents=True, exist_ok=True)
    PROGRESS_PATH.write_text(json.dumps(sorted(done), indent=2))


def _write_catalog(outcomes: dict) -> None:
    records = [o["record"] for o in outcomes.values() if o["outcome"] == "catalog"]
    excluded = [o["record"] for o in outcomes.values() if o["outcome"] == "excluded"]
    CATALOG_PATH.write_text(json.dumps(records, indent=2))
    EXCLUDED_PATH.write_text(json.dumps(excluded, indent=2))


def producer(work_queue: queue.Queue, already_processed: set[str]) -> None:
    aesthetic_score.set_device("cpu")  # see module docstring -- keeps GPU exclusive to VLM tagging

    parks = json.loads(PARKS_PATH.read_text())
    done = _load_progress()
    log.info("producer: %d/%d parks already complete from a prior run", len(done), len(parks))

    for i, (code, name) in enumerate(parks.items(), 1):
        if code in done:
            log.info("producer [%d/%d] %s (%s): already done, skipping", i, len(parks), code, name)
            continue

        log.info("producer [%d/%d] %s (%s): selecting candidates", i, len(parks), code, name)
        try:
            selected = curate.select_candidates_for_park(code, WORKDIR, THRESHOLD, FLOOR)
        except Exception:
            log.exception("producer: %s (%s) selection failed, skipping park", code, name)
            continue

        new_count = 0
        for candidate in selected:
            if candidate.id in already_processed:
                continue
            work_queue.put(candidate)
            new_count += 1
        log.info(
            "producer: %s queued %d new candidates (of %d selected)", code, new_count, len(selected)
        )
        work_queue.put((_PARK_DONE, code))

    work_queue.put(_ALL_DONE)
    log.info("producer: all %d parks queued, exiting", len(parks))


def consumer(work_queue: queue.Queue) -> None:
    outcomes, _ = pipeline._load_checkpoint(CHECKPOINT_PATH)
    done = _load_progress()
    images_dir = WORKDIR / "images"

    dedup = Deduplicator()
    if images_dir.exists():
        for existing in images_dir.glob("*.jpg"):
            dedup.is_duplicate(existing)

    n_tagged = 0
    while True:
        item = work_queue.get()
        if item is _ALL_DONE:
            break
        if isinstance(item, tuple) and item[0] == _PARK_DONE:
            code = item[1]
            _write_catalog(outcomes)
            _mark_done(code, done)
            log.info("consumer: %s tagging complete (%d catalog records so far)", code, n_tagged)
            continue

        candidate = item
        try:
            image_path = nps_client.download_image(candidate, images_dir)
        except Exception as e:
            log.warning("consumer: download failed for %s: %s", candidate.id, e)
            pipeline._write_checkpoint_line(
                CHECKPOINT_PATH, {"id": candidate.id, "outcome": "download_failed"}
            )
            continue

        dup_of = dedup.is_duplicate(image_path)
        if dup_of is not None and dup_of != image_path:
            log.info("consumer: %s duplicate of %s, skipping", candidate.id, dup_of.name)
            pipeline._write_checkpoint_line(
                CHECKPOINT_PATH, {"id": candidate.id, "outcome": "duplicate"}
            )
            continue

        try:
            record = pipeline.build_record(candidate, image_path)
        except Exception as e:
            log.warning("consumer: unexpected error building record for %s: %s", candidate.id, e)
            pipeline._write_checkpoint_line(
                CHECKPOINT_PATH, {"id": candidate.id, "outcome": "processing_error"}
            )
            continue
        if record is None:
            pipeline._write_checkpoint_line(
                CHECKPOINT_PATH, {"id": candidate.id, "outcome": "no_model_json"}
            )
            continue

        try:
            schema_validate.validate_record(record)
        except Exception as e:
            log.error("consumer: schema validation failed for %s: %s", candidate.id, e)
            pipeline._write_checkpoint_line(
                CHECKPOINT_PATH, {"id": candidate.id, "outcome": "invalid_schema"}
            )
            continue

        outcome = "excluded" if record["is_photograph"] is False else "catalog"
        entry = {"id": candidate.id, "outcome": outcome, "record": record}
        pipeline._write_checkpoint_line(CHECKPOINT_PATH, entry)
        outcomes[candidate.id] = entry
        if outcome == "catalog":
            n_tagged += 1

    _write_catalog(outcomes)
    log.info(
        "consumer: queue drained, %d total catalog records",
        len([o for o in outcomes.values() if o["outcome"] == "catalog"]),
    )


def main() -> int:
    load_dotenv(REPO_ROOT / ".env")  # WOPR_BASE_URL -- judge_image() needs this
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    _, already_processed = pipeline._load_checkpoint(CHECKPOINT_PATH)
    log.info("main: %d candidates already processed from a prior run", len(already_processed))

    work_queue: queue.Queue = queue.Queue()
    producer_thread = threading.Thread(
        target=producer, args=(work_queue, already_processed), daemon=True
    )
    producer_thread.start()

    consumer(work_queue)
    producer_thread.join()
    return 0


if __name__ == "__main__":
    sys.exit(main())
