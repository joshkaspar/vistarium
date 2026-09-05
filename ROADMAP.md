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

## Curated-scrape follow-ups (post 61-park run, 2026-09-05)

- ~~Find the right floor per park, not one flat number for all 61~~ --
  **investigated and largely resolved 2026-09-05**, see `DECISIONS.md`.
  The real problem wasn't the floor number itself -- it was that
  floor-topped-up candidates (deliberately included below the 5.4
  threshold to satisfy the floor) got silently stripped back out by
  `build_site.py`'s publish-time cutoff, which didn't know about the
  floor exception. Fixed with a per-park relaxed cutoff (5.2) applied
  only to parks still under floor after the standard cut. Remaining
  thinness after that (Wind Cave, Dry Tortugas, Mesa Verde-shaped
  cases) is genuine subject-matter mismatch -- these parks' defining
  feature is a cave/fort/cliff-dwelling `structure`, not an outdoor
  landscape vista -- not a floor-mechanics bug. Explicit stopping
  condition: if a park is still under 10 after the full four-step
  remediation (measure, relax cutoff, reconsider `structure`
  inclusion per-park, re-check small parks' excluded albums), it stays
  that way.
- **Ongoing monitoring: detect new files and albums NPS adds after the
  initial scrape.** The 61-park run treated each park as a one-time
  pass -- once selected/tagged, nothing re-checks whether NPS has since
  added new photos to existing albums or published new albums
  entirely. Real content will keep accumulating (NPS park staff upload
  regularly), and right now there's no mechanism to notice. Needs some
  kind of periodic re-crawl that diffs against what's already in
  `data/catalog.json`/`checkpoint.jsonl` (by candidate id) rather than
  reprocessing everything from scratch -- scope (how often, whether
  it's a cron-style job or a manual periodic run, how it interacts
  with the dedup-review workflow) not yet decided.

## Later sources (build order step 6)

- Library of Congress, Smithsonian, Met, AIC, NYPL -- each needs its own
  client module (like `nps_client.py`) and its own pass through the
  validation checkpoint, since their license taxonomies are messier than
  NPS's clean public-domain case (mix of true public domain and
  donor-restricted/rights-reserved items inside "open access"
  collections).
- Smithsonian and LOC need real API keys (see `.env.example`).

## Deferred, not needed yet

- **Producer should stream-queue candidates within a park, not just
  between parks.** `run_curated_scrape_remote.py`'s producer/consumer
  split (see `DECISIONS.md`, 2026-09-02) was built to stop the GPU
  sitting idle between parks, but a park's candidates still only reach
  the consumer's queue once that whole park's album triage + throttled
  thumbnail fetch + scoring pass is entirely finished. Exposed live by
  Yellowstone (park 59/61, 5,055 candidates -- the largest pool of the
  61-park run): the consumer drained the prior park's queue and sat
  idle for well over an hour waiting on Yellowstone's fetch phase alone,
  the exact failure mode the producer/consumer redesign was meant to
  prevent, just triggered by pool-size variance rather than strict
  per-park sequencing. Fix: have the producer push scored candidates
  onto the queue in batches (every N candidates, or every M minutes)
  as they're scored, instead of one bulk push at the end of a park's
  selection -- lets the consumer start on an oversized park's earliest
  results immediately rather than waiting on the whole pool. Not fixed
  mid-run -- this scrape is in its final stretch, not worth touching a
  running pipeline for; worth building into the next iteration.
- **Integrate duplicate detection into the pipeline itself, not just a
  post-hoc local tool.** `scripts/find_duplicates.py` +
  `dedup_review_server.py` (added 2026-09-04, see `DECISIONS.md`) run
  as a one-off pass against whatever's already published -- fine for
  cleaning up the current NPS-only corpus, but each new source added
  (Library of Congress, Smithsonian, etc.) will just accumulate its own
  fresh batch of duplicates the same way NPS did, needing another manual
  sweep. Once more sources land, this needs to become a real pipeline
  stage (run during/after tagging, before a record ever gets published,
  not after), not a periodic manual cleanup. Also revisit the visual
  side: exact-hash + EXIF-timestamp clustering is what's built now
  (perceptual hashing was tried and shelved -- didn't reliably separate
  known test cases, see `DECISIONS.md`), but CLIP-embedding similarity
  is still an open candidate for catching same-vantage-different-subject
  cases (e.g. the Denali tour-bus pair) that neither current method
  catches, and other sources may not have reliable EXIF timestamps the
  way NPS's professional photography does.
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
