# Roadmap

Feature ideas and known gaps that came up during the build, deliberately
kept out of the current task so they don't scope-creep it. Not
prioritized/dated -- see `DECISIONS.md` for things that were actually
decided.

## Near-term (build order steps 2+)

- ~~Run the 20-50 image validation checkpoint~~ -- **done 2026-08-30**: 22
  catalog + 4 excluded records, all hand-checked image-by-image against
  their recorded fields. 21/22 catalog records accurate; found and fixed
  three real bugs in the time-of-day evidence pipeline (see DECISIONS.md).
  One record (`c67d7db4...`, Morongo Basin) is still visibly wrong
  (`night` for an obviously bright midday desert photo) -- not a code
  bug, the source file's own `DateTimeOriginal` EXIF tag is simply wrong
  (camera clock error: `ExposureTime` 1/2000s and `OffsetTimeOriginal`
  disagreeing with `OffsetTime` both point to a misconfigured camera
  clock, not a parsing mistake). See "cross-check EXIF against the
  model's own guess" below.
- **Cross-check EXIF-derived time_of_day against the model's own visual
  guess.** The Morongo Basin case above shows EXIF can be confidently
  wrong even after fixing the DateTimeOriginal-vs-DateTime bug, if the
  source camera's clock itself was misconfigured. No way to know this
  from the EXIF alone -- but the model's independent visual read would
  likely have caught it (obviously bright/harsh light doesn't look like
  night). Worth a future pass where a large EXIF/model disagreement gets
  routed to `flagged_for_review`-style manual attention, the same way
  license ambiguity already is, rather than trusting EXIF unconditionally.
- ~~GitHub repo hosting split: WebP thumbnail generation~~ -- **done
  2026-08-31**: `build_site.py` renders `docs/thumbs/*.webp` from
  `thumbnail_crop_16x9` at build time (not stored as a pipeline output).
- ~~Static site (build order step 5)~~ -- **done 2026-08-31**: vanilla
  HTML/CSS/JS gallery in `docs/`, filterable by park, time of day, and
  people prominence, plus free-text tag search. `primary_subject` isn't
  a site filter since only `landscape` is published at all (see
  site-inclusion policy, `DECISIONS.md`). Lightbox links out to
  `source_url` for full resolution -- no full-size images in the repo.

- ~~Dominant/overall color filter~~ -- **added to the model 2026-09-01**
  (see `DECISIONS.md`): `dominant_color`, same field-ownership reasoning
  as `color_mode` after all -- a quick pixel-histogram prototype failed
  on a real image (a red-rock canyon with a big blue sky scored "cyan,"
  the sky's uniform pixels outvoting the darker but visually-dominant
  canyon), confirming this needed perceptual/compositional judgment,
  not literal pixel-counting. Not yet required in `schema.json` --
  needs a corpus backfill first, see the same `DECISIONS.md` entry.
- ~~NPS's own curated per-park photo galleries, not just keyword
  search~~ -- **superseded 2026-09-01**: the underlying gap (Acadia's
  results feeling thin) is fixed more foundationally by
  `nps_client.search_park_scenic()` -- see `DECISIONS.md`. The
  hand-curated per-park gallery pages
  (`nps.gov/<code>/learn/photosmultimedia/photogallery.htm`, hardcoded
  `albumIDs` fetched via `nps.gov/npgallery/api/album/metadata`) are
  still a real, further-curated layer on top of `Categories:Scenic` --
  NPS park staff's own picks, a subset worth surfacing distinctly (a
  possible future "staff picks" filter/badge) -- but not the primary
  fix anymore. Revisit once search_park_scenic() has been run against
  the current 9 parks and there's a sense of whether Categories:Scenic
  alone is enough.

## Later sources (build order step 6)

- Library of Congress, Smithsonian, Met, AIC, NYPL -- each needs its own
  client module (like `nps_client.py`) and its own pass through the
  validation checkpoint, since their license taxonomies are messier than
  NPS's clean public-domain case (mix of true public domain and
  donor-restricted/rights-reserved items inside "open access"
  collections).
- Smithsonian and LOC need real API keys (see `.env.example`).

## Deferred, not needed yet

- **Perceptual/near-duplicate detection.** `dedup.py` is exact-content
  (sha256) only -- won't catch a re-crop, re-compression, or
  watermarked re-upload of the same photo. Not built because there's no
  evidence yet it's a real problem at NPS-only volume; revisit if the
  validation checkpoint or later-volume runs show near-dupes slipping
  through.
- **9-way / rule-of-thirds crop_anchor.** Tested and rejected 2026-08-29
  -- see `DECISIONS.md`. Could be revisited with a reworded prompt that
  explicitly excludes brightness/glare as a signal, but not worth doing
  speculatively.
- **Self-hosted image mirror** on our own infrastructure, as an
  alternative to linking straight to source URLs, if source
  availability ever proves unreliable enough to matter.
- File-format sniffing hardening: two real NPS source files now found
  with a `.jpg` extension but actual TIFF-encoded bytes inside (one seen
  in an earlier unrelated project working with the same source, one in
  this project's own 2026-08-30 checkpoint batch). PIL handles both fine
  via content-sniffing and nothing has broken because of it, but it's no
  longer a one-off -- worth a defensive content-type check somewhere in
  the pipeline if a third instance turns up.
