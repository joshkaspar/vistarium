#!/usr/bin/env python3
"""Find duplicate/near-duplicate published photos, local-only review data.

Two clustering passes:

1. Exact -- identical thumbnail file bytes (md5), within each park.
   Operates entirely on docs/thumbs/*.webp, already synced locally.
   Zero ambiguity -- auto-hides every member except the highest-
   aesthetic_score one in hidden_ids.json.

2. Timestamp -- same park, EXIF capture time within --max-gap-minutes
   of each other (chained: A-B-C cluster together if each consecutive
   gap is within the window, even if A-C alone exceeds it). Requires
   real EXIF DateTimeOriginal/DateTimeDigitized -- a known clock-never-
   set default (e.g. 2000:01:01) is rejected, same sentinel logic
   exif_util.py already uses for time_of_day. Reads full-res images'
   EXIF headers on wopr over SSH (data/images/<id>.jpg) -- cheap, since
   getexif() doesn't need to decode pixel data, but this pass is NOT
   local-only like the exact pass. Timestamp clusters are NOT
   auto-hidden -- unlike identical images, "taken minutes apart" is a
   real judgment call (see the Thorofare Ridge cluster in DECISIONS.md,
   which has genuinely different photos alongside near-duplicates) --
   surfaced in the review UI with everything defaulted to kept.

A perceptual-hash (visual near-duplicate) pass was tried and shelved:
tested against the known Denali examples in DECISIONS.md (the Wonder
Lake burst, the aurora pair, and the confirmed-non-duplicate
2b8a099d...), no hash algorithm (phash/ahash/dhash/whash) cleanly
separated true duplicates from genuinely different shots -- the aurora
pair scored 20-44 bits apart (out of 64) on every algorithm, likely
because moving aurora/stars dominate the hash. Timestamp clustering
replaces it as the primary near-duplicate signal.

Writes data/dedup_clusters.json (cluster definitions) and updates
hidden_ids.json -- never deletes anything, and never overwrites a
decision already recorded for an id from a prior run. Review/adjust
via dedup_review_server.py.

Usage: uv run --extra dedup python scripts/find_duplicates.py
"""

import hashlib
import json
import subprocess
from collections import defaultdict
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_JSON = REPO_ROOT / "docs" / "data.json"
THUMBS_DIR = REPO_ROOT / "docs" / "thumbs"
CLUSTERS_PATH = REPO_ROOT / "data" / "dedup_clusters.json"
HIDDEN_IDS_PATH = REPO_ROOT / "hidden_ids.json"

DEFAULT_MAX_GAP_MINUTES = 15

# Companion script that must already be copied to wopr:/home/josh/vistarium-repo/scripts/
# (scp scripts/_remote_exif_batch.py wopr:/home/josh/vistarium-repo/scripts/) --
# reads ids from stdin, looks up EXIF on wopr where the full-res originals
# actually live, prints {id: iso_timestamp_or_null} JSON to stdout.
_REMOTE_PYTHON = "/home/josh/aesthetic-pilot/.venv/bin/python"
_REMOTE_REPO = "/home/josh/vistarium-repo"


def _load_records() -> list[dict]:
    records = json.loads(DATA_JSON.read_text())
    return [r for r in records if (THUMBS_DIR / Path(r["thumb"]).name).exists()]


def _exact_clusters(records: list[dict]) -> list[dict]:
    by_md5: dict[str, list[dict]] = defaultdict(list)
    for r in records:
        raw = (THUMBS_DIR / Path(r["thumb"]).name).read_bytes()
        by_md5[hashlib.md5(raw).hexdigest()].append(r)

    clusters = []
    for members in by_md5.values():
        if len(members) < 2:
            continue
        clusters.append(_make_cluster(members, "exact"))
    return clusters


def _fetch_exif_timestamps(ids: list[str]) -> dict[str, datetime | None]:
    remote_cmd = f"cd {_REMOTE_REPO} && {_REMOTE_PYTHON} scripts/_remote_exif_batch.py"
    result = subprocess.run(
        ["ssh", "wopr", remote_cmd],
        input="\n".join(ids),
        capture_output=True,
        text=True,
        check=True,
    )
    raw = json.loads(result.stdout)
    return {k: (datetime.fromisoformat(v) if v else None) for k, v in raw.items()}


def _timestamp_clusters(records: list[dict], max_gap_minutes: int) -> list[dict]:
    timestamps = _fetch_exif_timestamps([r["id"] for r in records])

    by_park: dict[str, list[dict]] = defaultdict(list)
    for r in records:
        if timestamps.get(r["id"]) is not None:
            by_park[r["park"]].append(r)

    clusters = []
    for park_records in by_park.values():
        park_records.sort(key=lambda r: timestamps[r["id"]])
        current: list[dict] = [park_records[0]]
        for prev, cur in zip(park_records, park_records[1:], strict=False):
            gap = (timestamps[cur["id"]] - timestamps[prev["id"]]).total_seconds() / 60
            if gap <= max_gap_minutes:
                current.append(cur)
            else:
                if len(current) >= 2:
                    clusters.append(_make_cluster(current, "timestamp"))
                current = [cur]
        if len(current) >= 2:
            clusters.append(_make_cluster(current, "timestamp"))
    return clusters


def _make_cluster(members: list[dict], cluster_type: str) -> dict:
    member_ids = [r["id"] for r in members]
    scores = [(r.get("aesthetic_score") or 0.0, r["id"]) for r in members]
    suggested_keep = max(scores)[1]
    return {
        "park": members[0]["park"],
        "type": cluster_type,
        "member_ids": member_ids,
        "suggested_keep": suggested_keep,
    }


def find_clusters(max_gap_minutes: int) -> list[dict]:
    records = _load_records()
    clusters = _exact_clusters(records) + _timestamp_clusters(records, max_gap_minutes)
    # Exact clusters first (quick, confident confirmations), then
    # timestamp clusters biggest-first (most likely to contain real
    # redundancy worth a human's attention).
    clusters.sort(key=lambda c: (c["type"] != "exact", -len(c["member_ids"])))
    return clusters


def _seed_hidden_ids(clusters: list[dict]) -> tuple[set[str], int]:
    existing: set[str] = set()
    if HIDDEN_IDS_PATH.exists():
        existing = set(json.loads(HIDDEN_IDS_PATH.read_text()))

    # Only exact clusters auto-hide -- timestamp clusters are a real
    # judgment call (see module docstring), left for manual review.
    newly_seeded = 0
    for c in clusters:
        if c["type"] != "exact":
            continue
        for mid in c["member_ids"]:
            if mid == c["suggested_keep"]:
                continue
            if mid not in existing:
                existing.add(mid)
                newly_seeded += 1
    return existing, newly_seeded


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--max-gap-minutes", type=int, default=DEFAULT_MAX_GAP_MINUTES)
    args = parser.parse_args()

    clusters = find_clusters(args.max_gap_minutes)
    CLUSTERS_PATH.parent.mkdir(parents=True, exist_ok=True)
    CLUSTERS_PATH.write_text(json.dumps(clusters, indent=2))

    hidden_ids, newly_seeded = _seed_hidden_ids(clusters)
    HIDDEN_IDS_PATH.write_text(json.dumps(sorted(hidden_ids), indent=2))

    exact = [c for c in clusters if c["type"] == "exact"]
    timestamp = [c for c in clusters if c["type"] == "timestamp"]
    print(
        f"{len(exact)} exact clusters, {len(timestamp)} timestamp clusters "
        f"({args.max_gap_minutes}min gap)"
    )
    print(f"{newly_seeded} newly auto-hidden ids (exact only, total hidden: {len(hidden_ids)})")
    print("Review at: uv run --extra dedup python scripts/dedup_review_server.py")


if __name__ == "__main__":
    main()
