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

- **Add `structure_present`/`structure_prominence` to the inference
  grammar and site filter.** Prompted by the `STRUCTURE_ALLOWED_PARKS`
  experiment (see item below and `DECISIONS.md`) being tried and then
  reverted -- a handful of
  `structure` shots (the Gateway Arch itself, Cliff Palace, Fort
  Jefferson) really were striking, individually, but smuggling them in
  under a per-park `primary_subject` exception was the wrong mechanism:
  it doesn't scale, and it undermines trusting the classification
  uniformly. A dedicated field lets a genuinely great structure photo
  be surfaced deliberately (its own filter/badge) without changing what
  counts as `landscape` or adding more per-park carve-outs. Josh does
  not want a full re-scan of the existing corpus to backfill this --
  he plans to do a manual visual pass himself at some point instead, so
  this is schema/filter work now, backfill later, on his own time.

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
  condition: if a park is still under 10 after measuring, relaxing the
  cutoff, and re-checking small parks' excluded albums, it stays that
  way. (A fourth step, a per-park `structure` inclusion carve-out, was
  tried and then reverted the same day -- see `DECISIONS.md` and the
  `structure_present`/`structure_prominence` item above -- so genuine
  subject-mismatch parks now simply stay thin rather than getting a
  subject-matter exception.)
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
- **`build_site.py` is slow at current corpus size (~5,600+ published
  records) -- worth profiling and speeding up.** Observed 2026-09-05:
  a full rebuild (structure-allowlist reversal, no other changes) ran
  ~30 minutes on wopr, CPU pegged at 100% the whole time, for what's
  conceptually a filter + per-record thumbnail render. Likely
  candidate: `_thumbnail()` re-decodes and re-crops a full-resolution
  original (some multi-hundred-megapixel scans, per the
  `DecompressionBombWarning`s seen live) for every published record on
  every rebuild, even when that record's crop/aesthetic data hasn't
  changed since the last build. Worth investigating incremental/cached
  thumbnail generation (skip re-render if the source file and crop
  params are unchanged) before the corpus grows further with more
  sources (LOC, Smithsonian, etc. below).
- **Watch for annotated/overlay graphics -- a real photo doctored with
  markup or promotional text, not a natural scene.** Two variants found
  so far, same underlying pattern (a genuine photo with something added
  on top, as distinct from the blank/degraded-scan problem covered by
  `content_visible`):
  - Administrative overlays: Grand Teton's "Kelly Parcel" land-
    acquisition series had 2 of 6 aerial photos with a bold red
    property-boundary line drawn over an otherwise genuine landscape
    shot (`28eb013c...`, `17ec2488...`, both hidden; found 2026-09-06).
  - Promotional/marketing graphics: a confirmed "Find Your Park" NPS
    marketing series at Shenandoah -- a real photo with a quote banner
    (John Muir, FDR) burned in at top and a "Find your park in
    Shenandoah National Park." caption bar at the bottom, one instance
    with the full 2016 NPS Centennial / "FIND YOUR PARK" logo lockup.
    4 confirmed and hidden 2026-09-07: `e86f3f13...` ("Sunset"),
    `e8823679...` ("Re-creation"), `e886b816...` ("Mountains"),
    `e873a926...` ("Skyline Drive") -- all portrait (9/16), all
    `flagged_for_review`, all credited generically to "NPS" or nothing.
    A same-park sweep of every other Shenandoah `flagged_for_review`
    record turned up no more (5 others checked were genuine photos
    flagged only for a person in frame). Worth the same sweep in other
    parks with a lot of flagged records if more of this series turn up.
    Notably `e86f3f13...` had also been swept into the 351-record
    `confirmed_ids.json` bulk migration (2026-09-06) without anyone
    having actually looked at it -- a reminder that bulk-migrating "not
    explicitly hidden" into "confirmed" only reflects the old status
    quo, not a real review, and the rest of that 351 may still contain
    other unreviewed problems like this one.
  - Interpretive/wayside exhibit panels: `9116671b-afc5-4cc7-beca-394b7461ee4d`
    (Acadia, "215 Sounds of the Sea") is a full wayside-sign design --
    title, body paragraph, an illustrated cave diagram, a "Safety Tips"
    box -- composited over a real wave-crash photo background. Heavier
    than a quote banner, closer to a museum placard graphic. Notably
    this one was NOT `flagged_for_review` at all (`license_confidence:
    confirmed`) -- it's a `primary_subject` misclassification, not a
    license-flag miss: schema.json's own "document" category is
    explicitly defined for "a photographed newspaper page, museum
    placard, interpretive sign, map, or screenshot," which is exactly
    what this is, but the model classified it as `landscape` instead,
    probably because the dominant visual content (the wave photo) reads
    as scenic despite the heavy text/graphic overlay covering much of
    the frame. Hidden 2026-09-07; no sibling instances found by a
    numbered-title sweep, but that was a narrow check (only 21 site-wide
    records match a leading-number title pattern, and only this one was
    actually a wayside-panel design) -- a real primary_subject miscall
    like this could exist elsewhere without the same title tell.
  Only caught by eye so far (a handful of examples across three
  different overlay styles); not worth an automated check yet at this
  sample size, but worth watching for more before deciding whether
  one's justified -- and worth planning an actual pass through the 351
  bulk-confirmed records at some point, since that migration was
  explicitly a status-quo carry-forward, not a review.
- **Repeat-photography/before-after composites aren't fully caught by
  any single photographer-field text signal -- three different
  credit-field patterns found so far, all for the same underlying
  two-panel composite genre.** The Denali exclusion (194 hidden,
  2026-09-06) relies on `"Rephoto photographer"` appearing in the
  `photographer` field, but two more examples found by eye the same day
  use entirely different phrasing: `2a972528...` (Kenai Fjords, "Exit
  Glacier," burned-in "May 12"/"September 23" date labels) is credited
  just `"NPS photographs."`, and `ce7d2fed...` (Wrangell-St. Elias,
  "McCarthy Road past and present") is credited
  `"Past - Bleakley Collection / Present - Mike Townsend"`. No single
  keyword covers all three, and each was only caught visually, not by
  any existing filter. A same-park or same-title deterministic check
  won't generalize either (these are all different titles/parks). The
  two-panel visual composite pattern itself (a hard horizontal or
  vertical dividing line roughly bisecting the frame, often with
  burned-in date/caption text near the seam) is the one thing all
  confirmed examples share -- worth exploring as a deterministic or
  model-judged check (a `is_composite`-style field, similar to
  `content_visible`) if more examples keep turning up. Not yet scoped.

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
