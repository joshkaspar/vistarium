"""Periodically pulls the curated-scrape results back from wopr
(run_curated_scrape_remote.py runs there -- see that file's docstring
for why) and publishes the site, so the live site keeps growing
throughout the multi-day run instead of waiting for it to finish.
Josh asked for this explicitly: "update the site periodically as the
images roll in" (2026-09-01).

Not part of the installed package. Run from the actual git checkout:

    uv run python scripts/sync_and_publish.py

Loops until interrupted (Ctrl-C) or until wopr's progress file shows
every park done. Each cycle: rsync data/catalog.json and data/images
from wopr, rebuild docs/ from the merged local corpus + newly-synced
images, and commit+push docs/ if anything actually changed.
"""

from __future__ import annotations

import logging
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
WOPR_REPO = "wopr:~/vistarium-repo/"
POLL_INTERVAL_S = 1800  # 30 min -- frequent enough to feel "periodic," cheap between cycles

log = logging.getLogger("vistarium.sync_and_publish")


def _sync_from_wopr() -> None:
    subprocess.run(
        ["rsync", "-a", f"{WOPR_REPO}data/catalog.json", str(REPO_ROOT / "data" / "catalog.json")],
        check=True,
    )
    subprocess.run(
        ["rsync", "-a", f"{WOPR_REPO}data/excluded_non_photo.json", str(REPO_ROOT / "data")],
        check=False,  # may not exist yet on the very first cycle
    )
    subprocess.run(
        ["rsync", "-a", f"{WOPR_REPO}data/images/", str(REPO_ROOT / "data" / "images") + "/"],
        check=True,
    )


def _remote_progress() -> tuple[int, int]:
    result = subprocess.run(
        [
            "ssh",
            "wopr",
            'python3 -c "import json; '
            "p=json.load(open('/home/josh/vistarium-repo/data/curated_scrape_progress.json')); "
            "n=json.load(open('/home/josh/vistarium-repo/national_parks.json')); "
            'print(len(p), len(n))"',
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return (0, 61)
    done, total = result.stdout.strip().split()
    return int(done), int(total)


def _rebuild_and_publish() -> bool:
    """Returns True if the site actually changed (and was committed+pushed)."""
    subprocess.run(["uv", "run", "vistarium-build-site"], cwd=REPO_ROOT, check=True)

    status = subprocess.run(
        ["git", "status", "--porcelain", "docs/"], cwd=REPO_ROOT, capture_output=True, text=True
    )
    if not status.stdout.strip():
        return False

    subprocess.run(["git", "add", "docs/"], cwd=REPO_ROOT, check=True)
    subprocess.run(
        [
            "git",
            "commit",
            "-m",
            "Curated scrape: publish latest results\n\n"
            "Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>\n"
            "Claude-Session: https://claude.ai/code/session_016JiigLaJ6DbBxYZv3sWgcN",
        ],
        cwd=REPO_ROOT,
        check=True,
    )
    subprocess.run(["git", "push"], cwd=REPO_ROOT, check=True)
    return True


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    while True:
        try:
            _sync_from_wopr()
        except subprocess.CalledProcessError:
            log.exception("rsync from wopr failed, will retry next cycle")
        else:
            try:
                published = _rebuild_and_publish()
                log.info("site %s", "published" if published else "unchanged, skipped commit")
            except subprocess.CalledProcessError:
                log.exception("site build/publish failed, will retry next cycle")

        done, total = _remote_progress()
        log.info("wopr progress: %d/%d parks done", done, total)
        if done >= total:
            log.info("all parks done -- final sync/publish cycle complete, exiting")
            return 0

        log.info("sleeping %ds until next cycle", POLL_INTERVAL_S)
        time.sleep(POLL_INTERVAL_S)


if __name__ == "__main__":
    sys.exit(main())
