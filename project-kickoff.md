# Claude Code Kickoff Prompt - Vistarium - Public Domain Wallpaper Curator

## Project

Build a pipeline that finds public domain / open-access landscape photographs from museum and archive APIs, classifies them with structured metadata, and presents them on a filterable static site (GitHub Pages). Start with the National Park Service (NPS) API only. Other sources (Library of Congress, Smithsonian, Met, Art Institute of Chicago, NYPL) come later, once the NPS pipeline is proven.

## Project Name

Marketing is an ongoing project, but so far I have come up with the following:
**Project Name:** Vistarium
**Project Tagline:** Open-access landscape photography, filtered by location, time of day, and subject.
## Division of labor (non-negotiable)

You (Claude Code) write and own all mechanical, deterministic logic:

- API calls, pagination, rate-limiting, retries
- Image download, dedup, EXIF extraction
- 16:9 crop-box computation
- JSON schema validation
- The harness that calls the local model and enforces its output shape

A local model (Qwen, via the WOPR endpoint) handles only narrow, bounded judgment calls, one image at a time, structured-output only:

- Time-of-day classification + evidence type
- License/rights ambiguity flag
- Subject/composition metadata (see schema below)

The model never makes multi-step decisions, never tracks counts or categories, never explores the API on its own. It receives one image + minimal metadata and returns one JSON object matching the schema. All counting, deduping, and category logic lives in your code, not in the model's output.

## Schema (finalize before scraping any real volume)

```json
{
  "id": "string",
  "source": "nps",
  "source_url": "string",
  "image_url": "string",
  "title": "string",
  "photographer": "string | null",
  "date": "string | null",
  "license": "string",
  "license_confidence": "confirmed | flagged_for_review",
  "license_evidence": "string",
  "time_of_day": "morning | afternoon | evening | night",
  "time_of_day_evidence": "caption | exif_timestamp | visual_inference",
  "primary_subject": "landscape | wildlife | structure | vehicle | human_activity",
  "people_present": "boolean",
  "people_prominence": "none | background | midground | foreground_focal",
  "crop_16x9": { "x": 0, "y": 0, "w": 0, "h": 0 },
  "park": "string",
  "tags": ["string"]
}
```

**Deterministic — Claude Code fills in from catalog metadata, never sent to the model:**
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

**Model judgment — the only thing sent per-request, grammar-constrained:**
```json
{
  "is_photograph": true,
  "time_of_day": "morning | afternoon | evening | night",
  "time_of_day_evidence": "caption | exif_timestamp | visual_inference",
  "license_confidence": "confirmed | flagged_for_review",
  "license_evidence": "string",
  "primary_subject": "landscape | wildlife | structure | vehicle | human_activity",
  "people_present": true,
  "people_prominence": "none | background | midground | foreground_focal",
  "crop_anchor": "center | top | bottom | left | right",
  "frame_type": "full_bleed | matted | multi_panel | stereograph",
  "tags": ["string"]
}
```


Design `license_confidence` / `license_evidence` to generalize past NPS's clean public-domain case — LOC, Smithsonian, Met, AIC, and NYPL all mix true public domain with donor-restricted or rights-reserved items inside "open access" collections. Anything not clearly and confidently public domain gets `flagged_for_review`, never silently decided.

`primary_subject` and `people_prominence` are metadata for site filtering, not an accept/reject gate. Do not have the model reject images outright based on people being present — record composition data and let filtering happen in the site UI.

## Build order

1. **NPS scraper + local model harness.** Get the mechanical pipeline and the model judgment step working end-to-end against NPS only.
2. **Validation checkpoint.** Before scaling up, run a batch of 20–50 images and hand-check: schema compliance, time-of-day accuracy, and especially the license flag-for-review behavior. Do not proceed to volume until this batch is clean.
3. **GitHub repo setup.** Structure:
    - Repo/Pages holds small preview thumbnails (WebP, a few hundred KB each) + the JSON metadata index only.
    - Full-resolution images are NOT stored in the repo. Link to the original source URL, or to a self-hosted copy on Zuul behind the existing Cloudflare Tunnel if guaranteed availability matters more than linking to source.
    - Reason: GitHub Pages has a ~1GB published-site soft limit and ~100GB/month soft bandwidth limit. Thousands of full-res wallpapers will blow past both.
4. **Run the pipeline at volume on NPS**, with periodic spot-checks (see maintenance doc — audit weight should stay highest on the license flag, indefinitely, even after everything else earns trust).
5. **Build the site interface**: static, JSON-driven, filterable by park, time of day, primary subject, people prominence, and tags. No backend needed.
6. **Add additional sources** (LOC, Smithsonian, Met, AIC, NYPL) one at a time, re-running the validation checkpoint (step 2) against each new source before scaling it, since their license taxonomies are messier than NPS's.

## Maintainability requirements (build these in from the first commit)

- Two-class commit discipline: mechanical commits vs. decision commits, following the same pattern as `AGENT_DECISION_POLICY.md` and `DECISIONS.md` from the WOPR project. Any non-mechanical call (schema shape, hosting split, prominence bucketing, etc.) gets a decision commit with a trailer explaining why.
- `schema.json` checked into the repo as the versioned source of truth. Changes to it are decision commits, never silent.
- Tests for every mechanical component: crop math, pagination, dedup, license-field parsing, schema validation. These are deterministic — they should have real test coverage, not just "it worked when I ran it."
- Pin dependencies (`requirements.txt` / `pyproject.toml` with locked versions).
- Set up `ruff`/`black` (or equivalent) from the start so all generated code stays consistent.
- `.env` for secrets (API keys), gitignored from commit one. Never let a key land in a script.
- README explains _why_, not just _what_ — document the reasoning behind the thumbnail/link split, the schema choices, and the build order, not just setup steps.
- Keep a `ROADMAP.md` separate from `DECISIONS.md` for feature ideas that come up mid-build, so they don't scope-creep into the current task.
- Confirm the repo builds and runs from a genuinely clean `git clone` (no local `.env`, no leftover venv) before adding the second source (step 6) and again before the site goes live.
  
