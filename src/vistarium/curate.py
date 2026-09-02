"""Curated-scale candidate selection: album triage -> thumbnail fetch ->
aesthetic pre-scoring -> threshold-with-floor selection, all before any
full-resolution download or VLM call.

Exists because a single Categories:Scenic search can return hundreds of
thousands of candidates NPS-wide, and processing all of them through the
per-image VLM judgment call was never the design (see DECISIONS.md,
2026-09-01). This module produces a small, high-quality shortlist from a
much larger pool cheaply -- title/description triage costs nothing,
thumbnails are ~78KB each instead of a full-res original's 1-2MB+, and
aesthetic scoring is a single batched model call, not per-image VLM
judgment. Only survivors ever reach pipeline.py's existing full-res
download + judge_image() loop, which is otherwise unchanged.

Every scored candidate for a park -- not just the ones that clear the
threshold -- is also written to a durable per-park manifest
(`<workdir>/scored_candidates/<PARK_CODE>.json`). Below-threshold
candidates are computed and then discarded from the selection path, but
their thumbnails are already downloaded and cached regardless -- without
this manifest, the scores themselves (title, park, aesthetic_score) would
be lost, and reconstructing them later would mean nothing worse than
re-scoring already-cached thumbnails, but reconstructing *which*
candidates existed at all would mean re-hitting NPS's album API. Josh
wants this data kept for three reasons: adjusting the aesthetic
threshold after the fact without rescanning, corpus-wide stats, and
reuse by a separate wildlife-photo pipeline off the same scan (see
project-kickoff.md's `primary_subject` taxonomy -- wildlife shots
scoring low on landscape-aesthetic terms are exactly the ones this
pipeline currently never even VLM-tags to find out). See DECISIONS.md,
2026-09-02.
"""

from __future__ import annotations

import dataclasses
import json
import logging
from pathlib import Path

from vistarium import aesthetic_score, album_triage, nps_client

log = logging.getLogger("vistarium.curate")


def select_by_threshold_with_floor(
    scored: list[tuple[nps_client.NPSCandidate, float]],
    threshold: float,
    floor: int,
) -> list[nps_client.NPSCandidate]:
    """Per park: keep everything scoring >= threshold; if that leaves
    fewer than `floor` candidates for that park, top up with its
    highest-scoring remainder until the floor is met (or its pool runs
    out). No park is excluded outright just for being less
    photogenic on average than others -- see DECISIONS.md, 2026-09-01.

    The returned list is sorted by score descending across all parks
    (not grouped by park) so downstream consumers -- namely
    pipeline.py's VLM tagging loop -- process the best-scoring
    candidates first, per Josh's "score them, then tag in score order"
    instruction."""
    by_park: dict[str, list[tuple[nps_client.NPSCandidate, float]]] = {}
    for candidate, score in scored:
        by_park.setdefault(candidate.park, []).append((candidate, score))

    selected: list[tuple[nps_client.NPSCandidate, float]] = []
    for _park, pairs in by_park.items():
        pairs.sort(key=lambda p: p[1], reverse=True)
        above = [(c, s) for c, s in pairs if s >= threshold]
        if len(above) >= floor:
            selected.extend(above)
            continue
        # Top up with the next-highest remaining, in score order, until
        # floor is met or the park's pool is exhausted.
        selected.extend(pairs[:floor])

    selected.sort(key=lambda p: p[1], reverse=True)
    return [c for c, _s in selected]


def _write_scored_manifest(
    park_code: str, workdir: Path, scored: list[tuple[nps_client.NPSCandidate, float]]
) -> Path:
    """Every scored candidate for this park, selected or not -- see the
    module docstring for why. Overwrites; select_candidates_for_park()
    only runs once per park within a given scrape."""
    manifest_dir = workdir / "scored_candidates"
    manifest_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = manifest_dir / f"{park_code}.json"
    entries = [{**dataclasses.asdict(c), "aesthetic_score": s} for c, s in scored]
    manifest_path.write_text(json.dumps(entries, indent=2))
    return manifest_path


def select_candidates_for_park(
    park_code: str,
    workdir: Path,
    threshold: float,
    floor: int,
    keywords_path: Path = album_triage.DEFAULT_KEYWORDS_PATH,
) -> list[nps_client.NPSCandidate]:
    """The full pre-filter pipeline for one park. Returns a shortlist of
    NPSCandidate -- the same shape pipeline.py's existing download/judge
    loop already consumes, so nothing downstream needs to change."""
    keywords = album_triage.load_keywords(keywords_path)

    albums = nps_client.list_park_albums(park_code)
    log.info("%s: %d albums found", park_code, len(albums))

    surviving_albums = []
    excluded_count = 0
    for album in albums:
        classification = album_triage.classify_album(album, keywords)
        if classification == "exclude":
            excluded_count += 1
            continue
        surviving_albums.append(album)
    log.info(
        "%s: %d albums excluded by keyword triage, %d proceed",
        park_code,
        excluded_count,
        len(surviving_albums),
    )

    by_id: dict[str, nps_client.NPSCandidate] = {}
    for album in surviving_albums:
        for candidate in nps_client.search_album(album.id, park_code=park_code):
            by_id.setdefault(candidate.id, candidate)
    candidates = list(by_id.values())
    log.info("%s: %d unique candidates across surviving albums", park_code, len(candidates))

    thumbs_dir = workdir / "thumbs_cache"
    paths = []
    for candidate in candidates:
        try:
            paths.append(nps_client.download_thumbnail(candidate, thumbs_dir))
        except Exception as e:
            log.warning("  thumbnail fetch failed for %s: %s", candidate.id, e)

    scores = aesthetic_score.score_all(paths)
    scored = [(c, scores[c.id]) for c in candidates if c.id in scores]
    log.info("%s: %d thumbnails scored", park_code, len(scored))

    manifest_path = _write_scored_manifest(park_code, workdir, scored)
    log.info("%s: wrote %d scored candidates to %s", park_code, len(scored), manifest_path)

    selected = select_by_threshold_with_floor(scored, threshold, floor)
    log.info(
        "%s: %d candidates selected (threshold=%.2f, floor=%d)",
        park_code,
        len(selected),
        threshold,
        floor,
    )
    return selected
