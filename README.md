# Vistarium

Open-access landscape photography, filtered by location, time of day, and subject.

**Site:** https://joshkaspar.github.io/vistarium/

A curated selection (not an exhaustive catalog) of public domain and open-access landscape photographs, currently sourced from the US National Park Service. Images are pulled via API, filtered by predicted aesthetic quality, then classified with structured metadata (time of day, subject, people, etc.) by a local vision model. Additional sources (Library of Congress, Smithsonian, Met, Art Institute of Chicago, NYPL) are planned.

## This repo vs. the pipeline

**This repo holds only the published site and its data** — the static HTML/CSS/JS, the generated thumbnails, `data.json`, and the small hand-curated override files (`hidden_ids.json`, `confirmed_ids.json`) that feed into it. It does not contain the scraper, classifier, or site-builder that produce this data.

That code lives in a separate repo, **[Cohobate](https://github.com/joshkaspar/Cohobate)** — the pipeline that scrapes source archives, curates and deduplicates candidates, classifies images with a local vision model, and builds this repo's `docs/` folder. Cohobate documents its own architecture, setup, and decision history; this repo is deliberately just the output plus the license/rights framing that governs reuse of that output.

The two repos were split apart (rather than always having been one) because a repo that grows a thumbnail set on every scheduled scrape handles that kind of history very differently than one holding active code development, and because the code (MIT) and the catalog (CC BY 4.0) are genuinely differently licensed — see [License & Rights](#license--rights) below.

## Data

Each record has two parts, populated separately, with one exception noted below.

**Catalog metadata** — pulled directly from the source API, unedited:

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

**Model judgment** — structured, grammar-constrained fields from a local vision model (see Cohobate), which only ever receives pixels (no filename, caption, or EXIF data is included in its prompt):

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

> **Exception:** `time_of_day` and `time_of_day_evidence`
> The model's guess is only used in the absence of EXIF or caption data. `time_of_day_evidence` records which source was actually used.

Plus a `thumbnail_crop_16x9` crop box, computed deterministically, and an optional `aesthetic_score` / `aesthetic_method` pair from a separate scoring stage. Full field-by-field definitions live in Cohobate's `schema.json` — the shape is documented there, alongside the pipeline that produces it, since this repo only holds the resulting data, not the schema's source of truth.

## Curation overrides

`hidden_ids.json` and `confirmed_ids.json` are small, hand-curated id lists that this repo owns and Cohobate's `build_site.py` reads (via `VISTARIUM_DATA_REPO_PATH` — see Cohobate's `SETUP.md`) when it rebuilds `docs/`:

- **`hidden_ids.json`** — ids to exclude from the site regardless of what the catalog says (manual dedup-review hides, or license/content concerns). Denylist.
- **`confirmed_ids.json`** — ids the model flagged `license_confidence: flagged_for_review` that a human has since reviewed and confirmed as safe to publish. Allowlist, opposite polarity from `hidden_ids.json`.

Both are real curation decisions, not regeneratable scrape output — they're versioned here because they're this repo's data, even though Cohobate is what reads and acts on them.

## Sorting

The site defaults to sorting by a predicted aesthetic score ("Aesthetic Rating (AI)"), with Date take and Date added as alternatives. The raw score itself isn't shown per photo — only used to order results.

## License & Rights

Three different things are licensed three different ways here — don't assume one license covers all of it:

- **The pipeline code** (in [Cohobate](https://github.com/joshkaspar/Cohobate)) is MIT-licensed — yours to reuse freely.
- **Vistarium's own catalog** (each curated, scored, classified record as a whole, not split field-by-field) is [CC BY 4.0](./LICENSE-DATA) — free to reuse, including commercially, with attribution.
- **The images themselves are not covered by either license.** Each image's rights status is recorded per-item in its own metadata (`license`, `license_confidence`, `license_evidence`), reflecting what the source institution states — not independently verified or guaranteed by this project.

Copyright-free status does not necessarily resolve every right that may apply — notably, a depicted person's right of privacy or publicity is separate from copyright and is not waived by an archive's public-domain designation. Users are responsible for verifying licenses on the original source and for their own lawful use of any downloaded material, including not infringing on the rights of third parties.

See [TERMS_OF_USE.md](./TERMS_OF_USE.md) for the full rights statement.

## Repo layout

```
vistarium/
├── docs/                    # the static site (GitHub Pages): index.html, app.js, style.css, data.json, thumbs/
├── hidden_ids.json          # curation override: denylist
├── confirmed_ids.json       # curation override: allowlist (flagged-for-review overrides)
├── STATUS.md                # auto-generated curation progress
├── TERMS_OF_USE.md
└── LICENSE-DATA             # CC BY 4.0, covers the catalog
```

## Status

See [`STATUS.md`](./STATUS.md) for live progress on processing images from all 61 national parks.
