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

- **Dominant/overall color filter.** Separate from `color_mode`
  (color vs. monochrome) -- some users will want to filter by the
  actual dominant hue of a landscape photo (blue, green, white/snow,
  etc.), e.g. for matching a wallpaper to a desktop theme. Undecided
  whether this is deterministic (a k-means/histogram dominant-color
  bucket over the pixels) or a model field. Worth noting from the
  `color_mode` experience (2026-08-31, `DECISIONS.md`): that field
  failed as pixel statistics because it required a semantic judgment
  ("is this a B&W photographic *process*"), which pixels alone can't
  answer -- a dominant-hue bucket is a different, more literal
  question (what color are most of the pixels), which is exactly what
  histogram/k-means color-quantization is good at. Likely a better fit
  for deterministic than `color_mode` was, but prototype and check
  against real examples before committing, same lesson as always.
- **NPS's own curated per-park photo galleries, not just keyword
  search.** Investigating why Acadia's results felt thin compared to
  its actual scenery (2026-09-01), Josh found NPS publishes curated
  photo galleries per park outside the generic search-results flow --
  e.g. https://www.nps.gov/acad/learn/photosmultimedia/photogallery.htm
  linking to https://www.nps.gov/media/photo/gallery.htm?pg=3539176&id=F810EE82-155D-451F-67D336A09FC76A3F,
  a gallery of individual items like
  https://www.nps.gov/media/photo/gallery-item.htm?pg=3539176&id=f810f106-155d-451f-67af-71bee230cbe6&gid=F810EE82-155D-451F-67D336A09FC76A3F.
  The item IDs are the same UUID format `nps_client.py` already uses
  (`npgallery.nps.gov/GetAsset/<id>/...`), and the asset itself is
  reachable at `nps.gov/npgallery/GetAsset/<id>/proxy/hires` -- a
  different derivative path than the `/Original` one currently used,
  worth checking for differences. So this isn't a new institution/API
  to onboard (unlike Library of Congress etc. below) -- it's the same
  underlying NPGallery asset store, reached via park-curated gallery
  pages (`gid`/`pg` params) instead of blind keyword search, and it
  plausibly surfaces park staff's own picks rather than whatever a
  scenic-keyword text search happens to match. Worth a scraper pass
  once someone maps the gallery-listing endpoint's actual shape (the
  two URLs above are the only samples on hand so far).

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
