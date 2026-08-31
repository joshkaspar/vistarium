# Vistarium

Open-access landscape photography, filtered by location, time of day, and subject.

A pipeline that finds public domain / open-access landscape photographs
from museum and archive APIs, classifies them with structured metadata,
and presents them on a filterable static site (GitHub Pages). Starting
with the National Park Service (NPS) API only; other sources (Library of
Congress, Smithsonian, Met, Art Institute of Chicago, NYPL) come later,
once the NPS pipeline is proven. See `ROADMAP.md` for what's not built
yet.


## Data

Every catalog record has two parts, populated separately and never
mixed together.

**Catalog metadata** -- pulled directly from the source API, unedited:

```json
{
  "id": "string",
  "source": "nps",
  "source_url": "string",
  "image_url": "string",
  "title": "string",
  "photographer": "string | null",
  "date": "string | null",
  "park": "string",
  "license": "string"
}
```

**Model judgment** -- the only fields sent to/returned by the local
vision model, grammar-constrained, one image at a time:

```json
{
  "is_photograph": true,
  "time_of_day": "morning | afternoon | evening | night",
  "time_of_day_evidence": "caption | exif_timestamp | visual_inference",
  "license_confidence": "confirmed | flagged_for_review",
  "license_evidence": "string",
  "primary_subject": "landscape | wildlife | structure | vehicle | human_activity | document",
  "people_present": true,
  "people_prominence": "none | background | midground | foreground_focal",
  "crop_anchor": "center | top | bottom | left | right",
  "frame_type": "full_bleed | matted | multi_panel | stereograph",
  "tags": ["string"]
}
```

Plus `thumbnail_crop_16x9` -- a pixel crop box, but computed
deterministically by Claude Code from `crop_anchor` and the image's real
dimensions, never produced by the model itself (see "Why crop_anchor,
not a crop box" below).

Full schema definition (with the reasoning behind every field) lives in
[`schema.json`](./schema.json).

## Why the mechanical/model split

Every step that can be deterministic is: API pagination, download,
dedup, EXIF reading, crop math, schema validation. A local vision model
(`qwen3.8-27b`, served from a home inference box) is used for
exactly three narrow, bounded judgment calls per image -- time-of-day,
license/rights ambiguity, and subject/composition metadata -- and
nothing else. It never explores an API, never counts, never tracks
state across images. This isn't a style preference: an earlier
side-by-side comparison of 5 local models on the same curation task
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
needed, on demand.

## License & Rights

Three different things are licensed three different ways here -- don't
assume one license covers all of it:

- **This repo's code** (`src/vistarium/`, tests, tooling) is
  [MIT-licensed](./LICENSE) -- yours to reuse freely.
- **Vistarium's own metadata** (`schema.json` and the classification
  fields it defines -- `time_of_day`, `primary_subject`, `tags`, and the
  rest) is [CC0](./LICENSE-DATA) -- public domain, no attribution
  required.
- **The images themselves are not covered by either license.** Each
  image's rights status is recorded per-item in its own metadata
  (`license`, `license_confidence`, `license_evidence`), reflecting what
  the source institution states -- not independently verified or
  guaranteed by this project.

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
the base `license` string in the first place -- see `DECISIONS.md`,
2026-08-30.

See [TERMS_OF_USE.md](./TERMS_OF_USE.md) for the full rights statement.

## Setup

Requires [`uv`](https://docs.astral.sh/uv/).

```bash
uv sync                 # installs pinned deps from uv.lock
cp .env.example .env    # no keys needed yet for NPS-only; see the file
uv run pytest           # run the test suite
uv run ruff check .     # lint
uv run ruff format .    # format
```

Run the pipeline (defaults to a 20-image batch, matching the
validation-checkpoint used at the beginning of the project):

```bash
uv run vistarium --limit 20
```

Writes `data/catalog.json` (schema-valid photograph records) and
`data/excluded_non_photo.json` (images the model flagged as not
photographs -- kept for audit, not shown on the site).

Build the static site from an existing catalog:

```bash
uv run vistarium-build-site
```

Writes `docs/data.json` and `docs/thumbs/*.webp` from
`data/catalog.json`, filtered to `primary_subject: landscape`. `docs/`
is what GitHub Pages serves.

## Project layout

- `schema.json` -- versioned source of truth for the catalog record shape. Changes here are decision commits (see `AGENT_DECISION_POLICY.md`).
- `src/vistarium/nps_client.py` -- NPS Gallery search + download (deterministic).
- `src/vistarium/dedup.py` -- exact-content dedup (deterministic).
- `src/vistarium/exif_util.py` -- caption/EXIF-based time-of-day evidence (deterministic; preferred over the model's visual guess when available).
- `src/vistarium/crop.py` -- crop-box math from `crop_anchor` (deterministic).
- `src/vistarium/model_client.py` -- the one call site for the local judgment model, grammar-constrained.
- `src/vistarium/schema_validate.py` -- validates records against `schema.json`.
- `src/vistarium/pipeline.py` -- orchestrates the above; CLI entry point.
- `src/vistarium/build_site.py` -- renders `docs/` (WebP thumbnails + `data.json`) from `data/catalog.json`, filtered to `primary_subject: landscape`.
- `docs/` -- the static site itself (GitHub Pages, vanilla HTML/CSS/JS, no build step).
- `tests/` -- real coverage for every deterministic component above.
- `TERMS_OF_USE.md` -- the full rights statement (see "License & Rights" above).
- `LICENSE` / `LICENSE-DATA` -- MIT (code) and CC0 (Vistarium's own metadata), respectively.
- `DECISIONS.md` / `ROADMAP.md` / `AGENT_DECISION_POLICY.md` -- decision log, future work, and the commit discipline behind both.

## Status

NPS scraper + model harness built, unit-tested, and validated by hand
against real data: a 22-image checkpoint batch and a 220-image
single-park run (Kenai Fjords), both fully image-by-image reviewed, not
just schema-checked. Three real bugs in the deterministic evidence
pipeline were found and fixed this way -- see `DECISIONS.md` for the
full narrative of each. Current dataset: 246 records across 6 parks.
Static site built from that dataset (`docs/`, GitHub Pages, 136
landscape records). Next: additional sources -- see `ROADMAP.md`.

## Project background

For the story of how this project is built (local-vs-cloud model
division of labor, the vibe-coding approach, why people aren't a reject
gate) see my upcoming post on [joshkaspar.dev](https://joshkaspar.dev).
