"""Drives the curated-scale pipeline (see curate.py, DECISIONS.md
2026-09-01) across every official U.S. National Park, one park at a
time, rebuilding and publishing the site after each park finishes.

Not part of the installed package -- a long-running (multi-day, NPS
throttle-bound) driver script, run directly:

    uv run python scripts/run_curated_scrape.py

Safe to interrupt and re-run: pipeline.run() checkpoints per-candidate
to data/checkpoint.jsonl, and this script tracks which parks have
already completed in data/curated_scrape_progress.json so a restart
skips finished parks instead of reprocessing them.
"""

from __future__ import annotations

import json
import logging
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

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


def _rebuild_and_publish(code: str, name: str) -> None:
    """Rebuilds docs/ from the latest catalog and commits+pushes it --
    Josh asked to "update the site periodically as the images roll
    in" (2026-09-01), and each finished park is a natural checkpoint.
    Only docs/ is tracked in git (data/ is gitignored -- runtime
    output, not source, see .gitignore)."""
    subprocess.run(["uv", "run", "vistarium-build-site"], cwd=REPO_ROOT, check=True)

    status = subprocess.run(
        ["git", "status", "--porcelain", "docs/"], cwd=REPO_ROOT, capture_output=True, text=True
    )
    if not status.stdout.strip():
        log.info("%s: site unchanged, nothing to commit", code)
        return

    subprocess.run(["git", "add", "docs/"], cwd=REPO_ROOT, check=True)
    subprocess.run(
        [
            "git",
            "commit",
            "-m",
            f"Curated scrape: add {name} (threshold={THRESHOLD}, floor={FLOOR})\n\n"
            "Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>\n"
            "Claude-Session: https://claude.ai/code/session_016JiigLaJ6DbBxYZv3sWgcN",
        ],
        cwd=REPO_ROOT,
        check=True,
    )
    subprocess.run(["git", "push"], cwd=REPO_ROOT, check=True)
    log.info("%s: site rebuilt, committed, and pushed", code)


def main() -> int:
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

        try:
            _rebuild_and_publish(code, name)
        except subprocess.CalledProcessError:
            log.exception("%s (%s): site rebuild/publish failed, continuing anyway", code, name)

        _mark_done(code, done)

    log.info("all %d parks processed", len(parks))
    return 0


if __name__ == "__main__":
    sys.exit(main())
