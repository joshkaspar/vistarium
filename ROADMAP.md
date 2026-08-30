# Roadmap

Feature ideas and known gaps that came up during the build, deliberately
kept out of the current task so they don't scope-creep it. Not
prioritized/dated -- see `DECISIONS.md` for things that were actually
decided and `project-kickoff.md` for the build order these feed into.

## Near-term (build order steps 2+)

- Run the 20-50 image validation checkpoint (build order step 2) and
  hand-check schema compliance, time-of-day accuracy, and especially the
  license flag-for-review behavior before scaling to real volume.
- GitHub repo hosting split: WebP thumbnail generation from
  `thumbnail_crop_16x9` isn't built yet -- `pipeline.py` computes the
  crop box but doesn't render/save the actual thumbnail file.
- Static site (build order step 5): filterable by park, time of day,
  primary subject, people prominence, tags. Not started.

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
- **Self-hosted image mirror on Zuul** (behind the existing Cloudflare
  Tunnel), as an alternative to linking straight to source URLs, if
  source availability ever proves unreliable enough to matter.
- File-format sniffing hardening: at least one real NPS source file was
  found with a `.jpg` extension but actual TIFF-encoded bytes inside
  (PIL handles this fine via content-sniffing, but worth a defensive
  check somewhere in the pipeline if this turns out to be common rather
  than a one-off).
