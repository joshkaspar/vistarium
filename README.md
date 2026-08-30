# Vistarium

Open-access landscape photography, filtered by location, time of day, and subject.

A pipeline that finds public domain / open-access landscape photographs
from museum and archive APIs, classifies them with structured metadata,
and presents them on a filterable static site (GitHub Pages). Starting
with the National Park Service (NPS) API only; other sources (Library of
Congress, Smithsonian, Met, Art Institute of Chicago, NYPL) come later,
once the NPS pipeline is proven. See `project-kickoff.md` for the full
original spec and `ROADMAP.md` for what's not built yet.

## Why the mechanical/model split

Every step that can be deterministic is: API pagination, download,
dedup, EXIF reading, crop math, schema validation. A local vision model
(`qwen3.8-27b`, served from a home inference box, `wopr`) is used for
exactly three narrow, bounded judgment calls per image -- time-of-day,
license/rights ambiguity, and subject/composition metadata -- and
nothing else. It never explores an API, never counts, never tracks
state across images. This isn't a style preference: an earlier
side-by-side comparison of 5 local models on the same curation task
(logged in the `wopr` repo's `DECISIONS.md`/`model_tests/`, 2026-08-21)
found every model except the one eventually chosen would fabricate
counts, skip visual verification entirely, or silently drop candidates
when given more autonomy than this. Keeping the model's job narrow and
structured-output-only is a correctness requirement, not a preference.

## Why crop_anchor, not a crop box

The model reports a coarse direction (`center`/`top`/`bottom`/`left`/
`right`) for where the subject sits, not pixel coordinates. Two things
ruled out asking for real coordinates: vision-language models are
unreliable at precise spatial grounding, and the site doesn't know in
advance what aspect ratio a given consumer (desktop wallpaper, mobile,
something else) will actually need. `src/vistarium/crop.py` computes
exact crop boxes deterministically from the anchor at whatever ratio is
needed, on demand. A 9-way variant (adding diagonal corners) was tested
against 79 real images and rejected -- see `DECISIONS.md`, 2026-08-29 --
because when the model did pick a corner, it tracked the single
brightest point in the frame (sun glare, a bright star) rather than the
actual subject, which is worse than the coarseness it was meant to fix.

## Why full-res images aren't stored in the repo

GitHub Pages has a ~1GB published-site soft limit and ~100GB/month soft
bandwidth limit; thousands of full-resolution wallpapers would blow past
both quickly. The repo holds small preview thumbnails (WebP, computed
from `thumbnail_crop_16x9`) and the JSON metadata index only. Full images
are linked to their original source URL.

## License & Rights

Images on Vistarium are sourced from open-access government and museum
archives (currently the National Park Service). Each image's recorded
license reflects what the source institution states, and is not
independently verified or guaranteed by this project.

Copyright-free status does not necessarily resolve every right that may
apply -- notably, a depicted person's right of privacy or publicity is
separate from copyright and is not waived by an archive's public-domain
designation. Users are responsible for verifying licenses on the
original source and for their own lawful use of any downloaded
material, including not infringing on the rights of third parties.

This isn't a hedge added after the fact -- it's the same approach NPS,
the Library of Congress, Smithsonian Open Access, Flickr Commons, and
Wikimedia Commons all take for exactly this situation (a disclaimer and
reuser responsibility, not per-image model releases), and it's why
`license_confidence`/`license_evidence` exist as separate fields from
the base `license` string in the first place -- see `schema.json` and
`DECISIONS.md`, 2026-08-30.

See [TERMS_OF_USE.md](./TERMS_OF_USE.md) for the full rights statement.

## Setup

Requires [`uv`](https://docs.astral.sh/uv/) (already available via `mise`
on this box).

```bash
uv sync                 # installs pinned deps from uv.lock
cp .env.example .env    # no keys needed yet for NPS-only; see the file
uv run pytest           # run the test suite
uv run ruff check .     # lint
uv run ruff format .    # format
```

Run the pipeline (defaults to a 20-image batch, matching the build
order's validation-checkpoint step -- see `project-kickoff.md`):

```bash
uv run vistarium --limit 20
```

Writes `data/catalog.json` (schema-valid photograph records) and
`data/excluded_non_photo.json` (images the model flagged as not
photographs -- kept for audit, not shown on the site).

## Project layout

- `schema.json` -- versioned source of truth for the catalog record shape. Changes here are decision commits (see `AGENT_DECISION_POLICY.md`).
- `src/vistarium/nps_client.py` -- NPS Gallery search + download (deterministic).
- `src/vistarium/dedup.py` -- exact-content dedup (deterministic).
- `src/vistarium/exif_util.py` -- caption/EXIF-based time-of-day evidence (deterministic; preferred over the model's visual guess when available).
- `src/vistarium/crop.py` -- crop-box math from `crop_anchor` (deterministic).
- `src/vistarium/model_client.py` -- the one call site for the local judgment model, grammar-constrained.
- `src/vistarium/schema_validate.py` -- validates records against `schema.json`.
- `src/vistarium/pipeline.py` -- orchestrates the above; CLI entry point.
- `tests/` -- real coverage for every deterministic component above.

## Status

NPS scraper + model harness built, unit-tested, and validated by hand
against real data: a 22-image checkpoint batch and a 220-image
single-park run (Kenai Fjords), both fully image-by-image reviewed, not
just schema-checked. Three real bugs in the deterministic evidence
pipeline were found and fixed this way -- see `DECISIONS.md` for the
full narrative of each. Current dataset: 246 records across 6 parks.
Next: the static site build and additional sources -- see `ROADMAP.md`.
