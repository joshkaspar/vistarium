# Architecture

How Vistarium's pipeline is built and why, for contributors (human or otherwise) working on the code. This is current-state documentation, not a chronological log — it gets updated in place as the design evolves. For dated records of specific judgment calls, see `DECISIONS.md`. For the narrative story of how this project came to be, see the upcoming post on joshkaspar.dev.

## Why `time_of_day` isn't purely model output

The model's prompt is explicit that it should judge time of day from the light in the image alone, not from any filename or caption it might infer — architecturally, `judge_image()` sends only the base64 pixels and a fixed prompt, nothing else. So the model's raw guess genuinely has zero access to catalog metadata at generation time.

But the model's guess isn't automatically what ships. `pipeline.build_record()` resolves the final `time_of_day`/`time_of_day_evidence` pair by priority, after the model has already run:

```python
def resolve_time_of_day(caption_text, exif_hour, model_time_of_day, frame_type):
    if exif_hour is not None and frame_type == "full_bleed":
        return exif_util.hour_to_bucket(exif_hour), "exif_timestamp"
    caption_bucket = exif_util.caption_time_of_day(caption_text)
    if caption_bucket:
        return caption_bucket, "caption"
    return model_time_of_day, "visual_inference"
```

`caption_text` comes from NPS's own catalog record; `exif_hour` comes from the image file itself. Both take priority over the model's visual guess when available — the model's answer only wins as a last-resort fallback, recorded as `time_of_day_evidence: "visual_inference"`. This is deliberate: an EXIF timestamp or a caption that says "sunset" is more reliable than a guess from pixels alone, so overriding the model here is a correctness improvement, not a violation of the mechanical/model split described above.

This is the one exception to that split, and it's confined to exactly one field pair — every other field in the model-judgment block (`is_photograph`, `license_confidence`, `primary_subject`, `people_prominence`, `crop_anchor`, `frame_type`, `color_mode`, `dominant_color`, `tags`) is untouched model output, unmodified after generation.

## Why the mechanical/model split

Every step that can be deterministic is: API pagination, download, dedup, EXIF reading, crop math, schema validation. A local vision model (`qwen3.8-27b`, served from a home inference box) is used for exactly three narrow, bounded judgment calls per image — time-of-day, license/rights ambiguity, and subject/composition metadata — and nothing else. It never explores an API, never counts, never tracks state across images.

This isn't a style preference: an earlier side-by-side comparison of 5 local models on the same curation task found every model except the one eventually chosen would fabricate counts, skip visual verification entirely, or silently drop candidates when given more autonomy than this. Keeping the model's job narrow and structured-output-only is a correctness requirement, not a preference.

## Why `crop_anchor`, not a crop box

The model reports a coarse direction (`center`/`top`/`bottom`/`left`/`right`) for where the subject sits, not pixel coordinates. Two things ruled out asking for real coordinates: vision-language models are unreliable at precise spatial grounding, and the site doesn't know in advance what aspect ratio a given consumer (desktop wallpaper, mobile, something else) will actually need. `src/vistarium/crop.py` computes exact crop boxes deterministically from the anchor at whatever ratio is needed, on demand.

## Why curated albums, not keywords or categories, drive content

Three search strategies exist in `nps_client.py`, in order of preference:

1. **`search_album()`** — NPGallery's own hand-curated albums (park staff's own picks, e.g. Acadia's "Cadillac Mountain" or "Acadia's Night Skies"). The primary content strategy. There's no reliable way to automate *which* albums are landscape-worthy versus administrative (a park's albums split roughly evenly between real scenic collections and things like staff meeting photos or ADA-accessibility parking-lot documentation, distinguishable by description but not by any pattern a script can key on) — so picking albums is a one-time human/Claude review per park, via `list_park_albums()`, not an automated filter.
2. **`search_park_scenic()`** — NPGallery's per-park `Categories:Scenic` tag. A smoke-test/volume tier, not the main strategy: broader and uncurated at the per-photo level, but still NPS's own classification, not a guess.
3. **`search_candidates()`** (`DEFAULT_TERMS` keyword search) — the original approach, kept for ad hoc use. Found live 2026-09-01: it missed Acadia's entire official "Night Skies" gallery (Milky Way, comet, and planet photos titled things like "Venus over Breakneck Pond") even when searching the literal terms "Acadia" + "night" — their titles simply don't contain the word "night." Keyword search can't find what it can't match on text.

Sampling behavior differs sharply by strategy, and this is deliberate, not an oversight:

- **`search_album()` (`nps_client.py:451`) applies no cap or sampling at all.** One request, `pagesize=2000`, no pagination loop — it returns every `Asset` in the album unconditionally. The docstring is explicit about why: "2000-image page size comfortably covers any real curated album (the largest seen so far is in the dozens, not hundreds)." This is correct given the rationale above — an album is already human-curated, so subsampling within it would discard some of that curation for no benefit. Its only current caller, `curate.select_candidates_for_park()`, doesn't apply `--limit` either; it filters by the aesthetic threshold instead (see "Curated selection" below).
- **`search_park_scenic()` and `search_candidates()` are a different story.** Their pools can be enormous — 15,242 `Categories:Scenic` images for Kenai Fjords alone, 308,802 site-wide. These two use pagination caps (`max_pages`/`max_pages_per_term`) to bound how many pages get fetched from NPS, and then `pipeline.py`'s `_sample_candidates()` draws a `random.sample()` from whatever came back, capped at `--limit`, before anything downloads or reaches the judgment model. Taking NPS's own default result order instead of sampling would risk a biased slice (e.g. only ever seeing one upload batch); random sampling avoids that.

So "does this path get capped and randomized" depends on which of the three strategies produced the candidate list — not a uniform rule. See `DECISIONS.md` for the investigation that led to the sampling approach for the uncurated paths.

## Curated selection, not an exhaustive catalog

NPGallery's real scale is far bigger than "a few hundred candidates per park" — `Categories:Scenic` alone returns 308,802 images site-wide, and a single park's albums can number in the hundreds. Processing all of that through the per-image judgment model was never the plan, and most of that volume isn't wallpaper-worthy regardless. `curate.py` runs a cheaper pre-filter before any of it reaches the model:

1. **Album keyword triage** (`album_triage.py`, `album_keywords.json`) — title/description only, no image bytes fetched. There's no reliable way to *automate* telling a scenic album from an administrative one (parking-lot documentation and a mountain-summit collection can have similarly plain titles), so this is a human-curated keyword list, not a learned classifier — exclude terms win over include terms, and anything ambiguous falls through to the next stage rather than being silently dropped.
2. **Thumbnail fetch** — NPGallery's `ProxyLoRes` derivative (~78KB) instead of the full-resolution original (1–2MB+); the aesthetic model doesn't need full resolution to score composition.
3. **Aesthetic pre-scoring** — the same predictor described below, batched, on thumbnails.
4. **Threshold-with-floor selection** — keep everything scoring above a threshold, but guarantee a minimum count per park (the floor) even if it doesn't clear that bar, so no park gets excluded outright just for averaging lower than others. Neither the threshold nor the floor is hardcoded anywhere — both are real judgment calls that depend on the actual score distribution once there's enough data to see it, not something to guess at in code.

Only survivors of all four stages ever get a full-resolution download and a real VLM judgment call.

Every NPGallery request (search, album listing, thumbnails, full-res downloads alike) is throttled through one shared rate limiter, `nps_client._http_request()`. NPGallery itself publishes no rate limit, but NPS's other public API (`developer.nps.gov`) documents 1000 requests/hour as its default, and NPGallery doesn't return the `X-RateLimit-*` headers that would let a caller observe its real quota in-flight — so that number is matched exactly as a conservative anchor, not padded, since a wrong guess here can't be self-corrected from response headers.

## Aesthetic scoring

The score comes from [LAION's aesthetics predictor v2](https://github.com/LAION-AI/aesthetic-predictor) — a CLIP-based model trained on human aesthetic ratings, the same tool NVIDIA's own [NeMo Curator](https://github.com/NVIDIA/NeMo-Curator) ships for dataset-quality filtering. It's disclosed right in the site's sort control ("Aesthetic Rating (AI)"), not buried in a separate page, and the raw score is never shown per-photo — only used to order results.

Run as its own pipeline stage (`vistarium-score-aesthetics`, needs the `aesthetic` extra — `uv sync --extra aesthetic`), not part of the main scrape/judge pipeline: it's a different model for a different purpose (ranking a large corpus, not structured per-image judgment), and torch/transformers are a genuinely heavy (~1–2GB) dependency not every contributor needs. It's used two ways: as the pre-filter inside `curate.py` (step 3 above), and as a standalone post-hoc backfill for records that predate the curation pipeline.

## Repo layout, annotated

- `schema.json` — versioned source of truth for the catalog record shape. Changes here are decision commits (see `AGENT_DECISION_POLICY.md`).
- `src/vistarium/nps_client.py` — NPS Gallery search + download (deterministic).
- `src/vistarium/album_triage.py` — keyword-based include/exclude/ambiguous classification of NPGallery albums (`album_keywords.json`), before any image bytes are fetched.
- `src/vistarium/curate.py` — the curated-scale selection pipeline: album triage → thumbnail fetch → aesthetic pre-scoring → threshold-with-floor selection.
- `src/vistarium/dedup.py` — exact-content dedup (deterministic).
- `src/vistarium/exif_util.py` — caption/EXIF-based time-of-day evidence (deterministic; preferred over the model's visual guess when available).
- `src/vistarium/crop.py` — crop-box math from `crop_anchor` (deterministic).
- `src/vistarium/model_client.py` — the one call site for the local judgment model, grammar-constrained.
- `src/vistarium/schema_validate.py` — validates records against `schema.json`.
- `src/vistarium/pipeline.py` — orchestrates the above; CLI entry point.
- `src/vistarium/aesthetic_score.py` — aesthetics-predictor scoring (GPU when available); its own optional pipeline stage (needs the `aesthetic` extra) — both the pre-filter `curate.py` uses and the post-hoc `vistarium-score-aesthetics` backfill.
- `src/vistarium/build_site.py` — renders `docs/` (WebP thumbnails + `data.json`) from `data/catalog.json`, filtered to `primary_subject: landscape`.
- `album_keywords.json` — versioned include/exclude keyword config for `album_triage.py`.
- `STATUS.md` — auto-generated curation progress (park-by-park checklist); regenerated by `scripts/sync_and_publish.py` every publish cycle, not hand-edited.
