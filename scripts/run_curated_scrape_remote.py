"""Runs the curated-scale pipeline (see curate.py, DECISIONS.md
2026-09-01) across every official U.S. National Park, one park at a
time. Meant to run on wopr (GPU host with torch installed -- the dev
VM has ~1GB free disk and can't hold torch, see pyproject.toml's
`aesthetic` extra comment), against wopr's own rsynced copy of the
repo, so it does no git operations itself. sync_and_publish.py (run
from the actual git checkout) periodically pulls data/catalog.json
and data/images back and publishes the site.

Not part of the installed package -- a long-running (multi-day, NPS
throttle-bound) driver script:

    ~/aesthetic-pilot/.venv/bin/python scripts/run_curated_scrape_remote.py

Safe to interrupt and re-run: pipeline.run() checkpoints per-candidate
to data/checkpoint.jsonl, and this script tracks which parks have
already completed in data/curated_scrape_progress.json so a restart
skips finished parks instead of reprocessing them.
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from dotenv import load_dotenv  # noqa: E402

from vistarium import pipeline  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
PARKS_PATH = REPO_ROOT / "national_parks.json"
PROGRESS_PATH = REPO_ROOT / "data" / "curated_scrape_progress.json"
THRESHOLD = 5.4
FLOOR = 10

log = logging.getLogger("vistarium.curated_scrape")


def _load_progress() -> set[str]:
    if PROGRESS_PATH.exists():
        return set(json.loads(PROGRESS_PATH.read_text()))
    return set()


def _mark_done(code: str, done: set[str]) -> None:
    done.add(code)
    PROGRESS_PATH.parent.mkdir(parents=True, exist_ok=True)
    PROGRESS_PATH.write_text(json.dumps(sorted(done), indent=2))


def main() -> int:
    load_dotenv(REPO_ROOT / ".env")  # WOPR_BASE_URL -- pipeline.run() needs this for judge_image()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    parks = json.loads(PARKS_PATH.read_text())
    done = _load_progress()
    log.info("%d/%d parks already complete from a prior run", len(done), len(parks))

    for i, (code, name) in enumerate(parks.items(), 1):
        if code in done:
            log.info("[%d/%d] %s (%s): already done, skipping", i, len(parks), code, name)
            continue

        log.info("[%d/%d] %s (%s): starting curated pipeline", i, len(parks), code, name)
        try:
            pipeline.run(
                limit=100_000,  # effectively unbounded -- take everything curate.py selects
                workdir=REPO_ROOT / "data",
                out_path=REPO_ROOT / "data" / "catalog.json",
                excluded_out_path=REPO_ROOT / "data" / "excluded_non_photo.json",
                terms=None,
                refresh_search=True,  # each park is a genuinely different search
                curate_park_code=code,
                threshold=THRESHOLD,
                floor=FLOOR,
            )
        except Exception:
            log.exception("%s (%s): pipeline run failed, skipping to next park", code, name)
            continue

        _mark_done(code, done)

    log.info("all %d parks processed", len(parks))
    return 0


if __name__ == "__main__":
    sys.exit(main())
