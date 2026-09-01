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
  "primary_subject": "landscape | wildlife | structure | vehicle | human_activity | document | detail",
  "people_present": true,
  "people_prominence": "none | background | midground | foreground_focal",
  "crop_anchor": "center | top | bottom | left | right",
  "frame_type": "full_bleed | matted | multi_panel | stereograph",
  "color_mode": "color | monochrome",
  "dominant_color": "red | orange | yellow | green | blue | purple | white | gray | black",
  "tags": ["string"]
}
```

Plus `thumbnail_crop_16x9` -- a pixel crop box, but computed
deterministically by Claude Code from `crop_anchor` and the image's real
dimensions, never produced by the model itself (see "Why crop_anchor,
not a crop box" below).

A third, optional pair -- `aesthetic_score` (float) and
`aesthetic_method` (`laion_predictor_v2 | manual_review | pending`) --
comes from neither: a separate CLIP-based aesthetics model
([`src/vistarium/aesthetic_score.py`](./src/vistarium/aesthetic_score.py)),
run as its own post-process stage, not required on every record. See
"Sort by Aesthetic Rating (AI)" below.

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

## Why curated albums, not keywords or categories, drive content

Three search strategies exist in `nps_client.py`, in order of
preference:

1. **`search_album()`** -- NPGallery's own hand-curated albums (park
   staff's own picks, e.g. Acadia's "Cadillac Mountain" or "Acadia's
   Night Skies"). The primary content strategy. There's no reliable way
   to automate *which* albums are landscape-worthy versus
   administrative (a park's albums split roughly evenly between real
   scenic collections and things like staff meeting photos or
   ADA-accessibility parking-lot documentation, distinguishable by
   description but not by any pattern a script can key on) -- so
   picking albums is a one-time human/Claude review per park, via
   `list_park_albums()`, not an automated filter.
2. **`search_park_scenic()`** -- NPGallery's per-park `Categories:Scenic`
   tag. A smoke-test/volume tier, not the main strategy: broader and
   uncurated at the per-photo level, but still NPS's own
   classification, not a guess.
3. **`search_candidates()`** (`DEFAULT_TERMS` keyword search) -- the
   original approach, kept for ad hoc use. Found live 2026-09-01: it
   missed Acadia's entire official "Night Skies" gallery (Milky Way,
   comet, and planet photos titled things like "Venus over Breakneck
   Pond") even when searching the literal terms "Acadia" + "night" --
   their titles simply don't contain the word "night." Keyword search
   can't find what it can't match on text.

A park's real candidate pool under any of these can be large (15,242
Categories:Scenic images for Kenai Fjords alone) -- nothing downloads
or reaches the judgment model until `--limit` lets it through, and
candidates are drawn by random sample from the pool, not NPS's own
(non-random) default result order. See `DECISIONS.md` for the full
investigation.

## Curated selection, not an exhaustive catalog

NPGallery's real scale is far bigger than "a few hundred candidates per
park" -- `Categories:Scenic` alone returns 308,802 images site-wide, and
a single park's albums can number in the hundreds. Processing all of
that through the per-image judgment model was never the plan, and most
of that volume isn't wallpaper-worthy regardless. `curate.py` runs a
cheaper pre-filter before any of it reaches the model:

1. **Album keyword triage** (`album_triage.py`, `album_keywords.json`)
   -- title/description only, no image bytes fetched. There's no
   reliable way to *automate* telling a scenic album from an
   administrative one (parking-lot documentation and a mountain-summit
   collection can have similarly plain titles), so this is a
   human-curated keyword list, not a learned classifier -- exclude
   terms win over include terms, and anything ambiguous falls through
   to the next stage rather than being silently dropped.
2. **Thumbnail fetch** -- NPGallery's `ProxyLoRes` derivative (~78KB)
   instead of the full-resolution original (1-2MB+); the aesthetic
   model doesn't need full resolution to score composition.
3. **Aesthetic pre-scoring** -- the same predictor described above,
   batched, on thumbnails.
4. **Threshold-with-floor selection** -- keep everything scoring above
   a threshold, but guarantee a minimum count per park (the floor) even
   if it doesn't clear that bar, so no park gets excluded outright just
   for averaging lower than others. Neither the threshold nor the floor
   is hardcoded anywhere -- both are real judgment calls that depend on
   the actual score distribution once there's enough data to see it,
   not something to guess at in code.

Only survivors of all four stages ever get a full-resolution download
and a real VLM judgment call -- unchanged from before.

Every NPGallery request (search, album listing, thumbnails, full-res
downloads alike) is throttled through one shared rate limiter,
`nps_client._http_request()`. NPGallery itself publishes no rate limit,
but NPS's other public API (`developer.nps.gov`) documents 1000
requests/hour as its default, and NPGallery doesn't return the
`X-RateLimit-*` headers that would let a caller observe its real quota
in-flight -- so that number is matched exactly as a conservative
anchor, not padded, since a wrong guess here can't be self-corrected
from response headers.

## Sort by Aesthetic Rating (AI)

The site defaults to sorting by a predicted-aesthetic score instead of
whatever order images were scraped in. As more, less-curated sources
get added, the ratio of wallpaper-worthy photos to mediocre ones will
drop; this is meant to let people find the good ones without the site
needing to be hand-curated.

The score comes from [LAION's aesthetics predictor
v2](https://github.com/LAION-AI/aesthetic-predictor) -- a CLIP-based
model trained on human aesthetic ratings, the same tool NVIDIA's own
[NeMo Curator](https://github.com/NVIDIA/NeMo-Curator) ships for
dataset-quality filtering. It's disclosed right in the sort control
("Aesthetic Rating (AI)"), not buried in a separate page, and the raw
score is never shown per-photo -- only used to order results. Run as
its own pipeline stage (`vistarium-score-aesthetics`, needs the
`aesthetic` extra -- `uv sync --extra aesthetic`), not part of the main
scrape/judge pipeline: it's a different model for a different purpose
(ranking a large corpus, not structured per-image judgment), and
torch/transformers are a genuinely heavy (~1-2GB) dependency not every
contributor needs.

## License & Rights

Three different things are licensed three different ways here -- don't
assume one license covers all of it:

- **This repo's code** (`src/vistarium/`, tests, tooling) is
  [MIT-licensed](./LICENSE) -- yours to reuse freely.
- **Vistarium's own catalog** (`schema.json`'s shape -- each curated,
  scored, classified record as a whole, not split field-by-field) is
  [CC BY 4.0](./LICENSE-DATA) -- free to reuse, including commercially,
  with attribution.
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
- `src/vistarium/album_triage.py` -- keyword-based include/exclude/ambiguous classification of NPGallery albums (`album_keywords.json`), before any image bytes are fetched.
- `src/vistarium/curate.py` -- the curated-scale selection pipeline: album triage -> thumbnail fetch -> aesthetic pre-scoring -> threshold-with-floor selection. See "Curated selection, not an exhaustive catalog" below.
- `src/vistarium/dedup.py` -- exact-content dedup (deterministic).
- `src/vistarium/exif_util.py` -- caption/EXIF-based time-of-day evidence (deterministic; preferred over the model's visual guess when available).
- `src/vistarium/crop.py` -- crop-box math from `crop_anchor` (deterministic).
- `src/vistarium/model_client.py` -- the one call site for the local judgment model, grammar-constrained.
- `src/vistarium/schema_validate.py` -- validates records against `schema.json`.
- `src/vistarium/pipeline.py` -- orchestrates the above; CLI entry point.
- `src/vistarium/aesthetic_score.py` -- aesthetics-predictor scoring (GPU when available), its own optional pipeline stage (needs the `aesthetic` extra) -- both the pre-filter `curate.py` uses and the post-hoc `vistarium-score-aesthetics` backfill.
- `src/vistarium/build_site.py` -- renders `docs/` (WebP thumbnails + `data.json`) from `data/catalog.json`, filtered to `primary_subject: landscape`.
- `album_keywords.json` -- versioned include/exclude keyword config for `album_triage.py`.
- `docs/` -- the static site itself (GitHub Pages, vanilla HTML/CSS/JS, no build step).
- `tests/` -- real coverage for every deterministic component above.
- `TERMS_OF_USE.md` -- the full rights statement (see "License & Rights" above).
- `LICENSE` / `LICENSE-DATA` -- MIT (code) and CC BY 4.0 (Vistarium's own catalog), respectively.
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
