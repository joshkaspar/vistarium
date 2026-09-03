# Vistarium

Open-access landscape photography, filtered by location, time of day, and subject.

**Site:** https://joshkaspar.github.io/vistarium/

A curated selection (not an exhaustive catalog) of public domain and open-access landscape photographs, currently sourced from the US National Park Service. Images are pulled via API, filtered by predicted aesthetic quality, then classified with structured metadata (time of day, subject, people, etc.) by a local vision model. Additional sources (Library of Congress, Smithsonian, Met, Art Institute of Chicago, NYPL) are planned.

## Documentation

| | |
|---|---|
| [`ARCHITECTURE.md`](./ARCHITECTURE.md) | How and why the pipeline is built the way it is |
| [`SETUP.md`](./SETUP.md) | Prerequisites and commands for running it yourself |
| [`ROADMAP.md`](./ROADMAP.md) | Planned features and sources |
| [`DECISIONS.md`](./DECISIONS.md) | Dated log of judgment calls, by people and AI models (mostly AI models) |
| [`CONTRIBUTING.md`](./CONTRIBUTING.md) | Why this project is open source but not open to contribution |
| upcoming post on [joshkaspar.dev](https://joshkaspar.dev/) | Watch this space for the story behind building this project |

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

**Model judgment** — structured, grammar-constrained fields from the local vision model, which only ever receives pixels (no filename, caption, or EXIF data is included in its prompt):

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

Plus a `thumbnail_crop_16x9` crop box, computed deterministically, and an optional `aesthetic_score` / `aesthetic_method` pair from a separate scoring stage. Full field-by-field definitions live in [`schema.json`](./schema.json).

## Sorting

The site defaults to sorting by a predicted aesthetic score ("Aesthetic Rating (AI)"), with Newest, Park, and Random as alternatives. The raw score itself isn't shown per photo — only used to order results.

## License & Rights

Three different things are licensed three different ways here — don't assume one license covers all of it:

- **This repo's code** (`src/vistarium/`, tests, tooling) is [MIT-licensed](./LICENSE) — yours to reuse freely.
- **Vistarium's own catalog** (`schema.json`'s shape — each curated, scored, classified record as a whole, not split field-by-field) is [CC BY 4.0](./LICENSE-DATA) — free to reuse, including commercially, with attribution.
- **The images themselves are not covered by either license.** Each image's rights status is recorded per-item in its own metadata (`license`, `license_confidence`, `license_evidence`), reflecting what the source institution states — not independently verified or guaranteed by this project.

Copyright-free status does not necessarily resolve every right that may apply — notably, a depicted person's right of privacy or publicity is separate from copyright and is not waived by an archive's public-domain designation. Users are responsible for verifying licenses on the original source and for their own lawful use of any downloaded material, including not infringing on the rights of third parties.

See [TERMS_OF_USE.md](./TERMS_OF_USE.md) for the full rights statement.

## Repo layout

```
vistarium/
├── schema.json              # versioned catalog record shape
├── album_keywords.json      # album triage keyword config
├── src/vistarium/           # pipeline code — see ARCHITECTURE.md for what each module does
├── docs/                    # the static site (GitHub Pages)
├── tests/
├── ARCHITECTURE.md          # pipeline design and reasoning
├── SETUP.md                 # prerequisites and how to run it
├── DECISIONS.md             # dated log of judgment calls
├── ROADMAP.md               # what's not built yet
├── STATUS.md                # auto-generated curation progress
├── TERMS_OF_USE.md
└── LICENSE / LICENSE-DATA
```

## Status

See [`STATUS.md`](./STATUS.md) for live progress on processing images from all 61 national parks.
