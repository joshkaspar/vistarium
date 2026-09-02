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

import json
import logging
import re
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
WOPR_HOST = "wopr"
WOPR_REPO_PATH = "/home/josh/vistarium-repo"
WOPR_PYTHON = "/home/josh/aesthetic-pilot/.venv/bin/python"
POLL_INTERVAL_S = 1800  # 30 min -- frequent enough to feel "periodic," cheap between cycles
STATUS_PATH = REPO_ROOT / "STATUS.md"
PARKS_PATH = REPO_ROOT / "national_parks.json"

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


def _remote_done_codes() -> list[str]:
    """Codes of parks the wopr-side scrape has fully finished tagging
    (see run_curated_scrape_remote.py -- a park is marked done only once
    the consumer, not the producer, finishes it), or [] if the progress
    file doesn't exist yet (before the first park completes)."""
    result = subprocess.run(
        [
            "ssh",
            "wopr",
            'python3 -c "import json; '
            "print(json.dumps(json.load(open("
            "'/home/josh/vistarium-repo/data/curated_scrape_progress.json'))))\"",
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return []
    return json.loads(result.stdout)


def _remote_in_progress_code() -> str | None:
    """The park code from the producer's most recent "selecting
    candidates" log line, or None if it can't be determined.

    Originally this was inferred as "the first park in
    national_parks.json order that isn't done yet" -- but that breaks
    permanently once any single park fails outright (see DECISIONS.md,
    2026-09-02: Denali hit a corrupted thumbnail that crashed its whole
    scoring batch, producer.py's except-and-skip moved on without ever
    marking it done, and every status check afterward kept reporting
    Denali as "in progress" while parks that had actually finished --
    Glacier, Glacier Bay -- showed as not even started). Reading the
    log directly is ground truth regardless of completion order."""
    result = subprocess.run(
        [
            "ssh",
            WOPR_HOST,
            f"grep 'selecting candidates' {WOPR_REPO_PATH}/curated_scrape.log | tail -1",
        ],
        capture_output=True,
        text=True,
    )
    match = re.search(r"producer \[\d+/\d+\] (\w+)", result.stdout)
    return match.group(1) if match else None


def _write_status_md(done_codes: list[str], in_progress: str | None) -> None:
    """Renders STATUS.md from national_parks.json (park order/names,
    already local) + done_codes/in_progress (from wopr) + docs/data.json's
    own record count (already local, just synced). Regenerated every
    cycle -- see sync_and_publish.py's module docstring for why."""
    parks = json.loads(PARKS_PATH.read_text())
    done = set(done_codes)

    try:
        record_count = len(json.loads((REPO_ROOT / "docs" / "data.json").read_text()))
    except FileNotFoundError:
        record_count = 0

    now = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")

    lines = [
        "# Curation Status",
        "",
        f"_Auto-generated by `scripts/sync_and_publish.py` -- do not edit directly. "
        f"Last updated: {now}._",
        "",
        f"**{len(done)} of {len(parks)} national parks curated -- "
        f"{record_count:,} photos published so far.**",
        "",
        "See [DECISIONS.md](DECISIONS.md) for how curation works (album triage, "
        "aesthetic-score threshold, VLM tagging).",
        "",
        "## Parks",
        "",
    ]
    for code, name in parks.items():
        if code in done:
            lines.append(f"- [x] {name}")
        elif code == in_progress:
            lines.append(f"- \N{ALARM CLOCK} {name} (in progress)")
        else:
            lines.append(f"- [ ] {name}")
    lines.append("")

    STATUS_PATH.write_text("\n".join(lines))


def _commit_and_push() -> bool:
    """Returns True if the site actually changed (and was committed+pushed).
    Building already happened on wopr (_build_on_wopr) -- this only
    commits whatever landed in docs/ from that rsync, plus STATUS.md."""
    status = subprocess.run(
        ["git", "status", "--porcelain", "docs/", "STATUS.md"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    if not status.stdout.strip():
        return False

    subprocess.run(["git", "add", "docs/", "STATUS.md"], cwd=REPO_ROOT, check=True)
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
        done_codes = _remote_done_codes()
        try:
            _build_on_wopr()
        except subprocess.CalledProcessError:
            log.exception("build/sync from wopr failed, will retry next cycle")
        else:
            try:
                _write_status_md(done_codes, _remote_in_progress_code())
                published = _commit_and_push()
                log.info("site %s", "published" if published else "unchanged, skipped commit")
            except subprocess.CalledProcessError:
                log.exception("commit/push failed, will retry next cycle")

        total = len(json.loads(PARKS_PATH.read_text()))
        done = len(done_codes)
        log.info("wopr progress: %d/%d parks done", done, total)
        if done >= total:
            log.info("all parks done -- final sync/publish cycle complete, exiting")
            return 0

        log.info("sleeping %ds until next cycle", POLL_INTERVAL_S)
        time.sleep(POLL_INTERVAL_S)


if __name__ == "__main__":
    sys.exit(main())
