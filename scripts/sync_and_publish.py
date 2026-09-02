"""Periodically builds the site on wopr (run_curated_scrape_remote.py
runs there -- see that file's docstring for why) and pulls back just
the compact result, so the live site keeps growing throughout the
multi-day run instead of waiting for it to finish. Josh asked for this
explicitly: "update the site periodically as the images roll in"
(2026-09-01).

Deliberately does NOT rsync data/images (the full-res originals, ~15KB
each up to a few MB, tens of thousands of them) to the dev VM -- that
filled the dev VM's disk to 100% and broke every sync cycle for hours
before anyone noticed (see DECISIONS.md, 2026-09-02). The dev VM never
needs full-res images: docs/index.html, app.js, and style.css (the
site's real source) live here and get pushed *to* wopr once up front;
build_site.py itself runs entirely on wopr against wopr's own
data/catalog.json + data/images, and only its output --
docs/data.json and docs/thumbs/*.webp, both small -- comes back.

Not part of the installed package. Run from the actual git checkout:

    uv run python scripts/sync_and_publish.py

Loops until interrupted (Ctrl-C) or until wopr's progress file shows
every park done.
"""

from __future__ import annotations

import logging
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
WOPR_HOST = "wopr"
WOPR_REPO_PATH = "/home/josh/vistarium-repo"
WOPR_PYTHON = "/home/josh/aesthetic-pilot/.venv/bin/python"
POLL_INTERVAL_S = 1800  # 30 min -- frequent enough to feel "periodic," cheap between cycles

log = logging.getLogger("vistarium.sync_and_publish")


def _build_on_wopr() -> None:
    subprocess.run(
        [
            "ssh",
            WOPR_HOST,
            f"cd {WOPR_REPO_PATH} && {WOPR_PYTHON} -c "
            "\"import sys; sys.path.insert(0, 'src'); "
            "from vistarium.build_site import build_site; "
            "from pathlib import Path; "
            "n = build_site(Path('data/catalog.json'), Path('data/images'), Path('docs')); "
            "print(f'wopr: wrote {n} records')\"",
        ],
        check=True,
    )
    subprocess.run(
        [
            "rsync",
            "-a",
            f"{WOPR_HOST}:{WOPR_REPO_PATH}/docs/data.json",
            str(REPO_ROOT / "docs" / "data.json"),
        ],
        check=True,
    )
    subprocess.run(
        [
            "rsync",
            "-a",
            "--delete",
            f"{WOPR_HOST}:{WOPR_REPO_PATH}/docs/thumbs/",
            str(REPO_ROOT / "docs" / "thumbs") + "/",
        ],
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


def _commit_and_push() -> bool:
    """Returns True if the site actually changed (and was committed+pushed).
    Building already happened on wopr (_build_on_wopr) -- this only
    commits whatever landed in docs/ from that rsync."""
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
            _build_on_wopr()
        except subprocess.CalledProcessError:
            log.exception("build/sync from wopr failed, will retry next cycle")
        else:
            try:
                published = _commit_and_push()
                log.info("site %s", "published" if published else "unchanged, skipped commit")
            except subprocess.CalledProcessError:
                log.exception("commit/push failed, will retry next cycle")

        done, total = _remote_progress()
        log.info("wopr progress: %d/%d parks done", done, total)
        if done >= total:
            log.info("all parks done -- final sync/publish cycle complete, exiting")
            return 0

        log.info("sleeping %ds until next cycle", POLL_INTERVAL_S)
        time.sleep(POLL_INTERVAL_S)


if __name__ == "__main__":
    sys.exit(main())
