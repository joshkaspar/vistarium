# Decisions

Append-only, newest at bottom. See `AGENT_DECISION_POLICY.md` for the
format and rules. This file is the primary input for any later
narrative-assembly (retrospective, write-up) process.

## 2026-08-29 -- Project kickoff: model choice, schema split, crop_anchor scheme
Commit: 55a3581
[agent-drafted, Josh-approved]

Context: before writing any pipeline code, worked through whether wopr
already had a model suited to the three judgment calls this project
needs (time-of-day + evidence, license/rights ambiguity, subject/
composition), and iterated on the exact JSON schema those calls should
produce.

**Model choice**: no audition run. `qwen3.8-27b:low` was already
validated for this exact task family twice over on this same inference
box, in earlier unrelated projects: a head-to-head vision-accuracy eval
against `qwen2.5-vl-32b`/`qwen-vl` on real-world listing photos
(2026-08-17), and a 5-model side-by-side on a direct predecessor of this
project's own curation task (2026-08-21), where it was the only model
that ever actually opened and visually inspected an image before
judging it. A follow-up smoke test (79 real images, see below) confirmed
grammar-constrained structured output works reliably against it.

Decision: use `qwen3.8-27b:low` (wopr's existing default) as the judgment model; no new model audition
Alternatives-considered: qwen2.5-vl-32b, qwen-vl, muse-glimmer, gemma-31b, gpt-oss-20b (all previously ruled out on this box for this task family)
Rationale: already validated twice on directly comparable tasks; re-auditioning would repeat work already done
Outcome: resolved

**Schema split**: the originally drafted schema mixed deterministic
catalog fields (`id`, `source_url`, `title`, `photographer`, `date`,
`park`) with model-judgment fields in one flat object. Split so only
judgment fields are ever sent to/returned by the model; Claude Code
fills in and merges the rest from source metadata. Also added
`is_photograph` as a gate after a smoke-test run showed a watercolor
painting getting full `time_of_day`/`primary_subject` judgments with
nothing to flag it as non-photographic.

Decision: model receives/returns only `is_photograph`, `time_of_day(_evidence)`, `license_confidence`/`license_evidence`, `primary_subject`, `people_present`/`people_prominence`, `crop_anchor`, `frame_type`, `tags`; everything else is deterministic
Alternatives-considered: one flat schema covering both catalog metadata and judgment fields (the original kickoff draft)
Rationale: model restating known catalog fields adds hallucination risk for zero benefit; is_photograph gate closes a real gap a live test exposed
Outcome: resolved

**crop_anchor, not a crop box**: rejected the original `crop_16x9: {x,y,w,h}` field. Target aspect ratio isn't known at judgment time (desktop/mobile/other consumers differ), and precise pixel coordinates are exactly the kind of fine spatial grounding VLMs are unreliable at. A 9-way variant (5 cardinal directions + 4 diagonal corners) was then built and tested against 79 real images specifically to check whether the model could usefully add corner resolution. Result: corners were used in only 6/79 cases, and in the 2 checked visually against the actual image, the anchor tracked the single brightest point in frame (sun glare on `evening/06_...`, a bright star on `night_37_...`) rather than the real subject -- in one case even contradicting the model's own `license_evidence` text, which correctly located two people in the upper-left of the same image the crop anchor called `topright`.

Decision: `crop_anchor` stays 5-way (`center`/`top`/`bottom`/`left`/`right`); Claude Code computes exact pixel crop boxes on demand from the anchor plus a target ratio
Alternatives-considered: raw pixel crop box, 9-way anchor with diagonal corners
Rationale: the corner tier's failure mode (tracking brightness, not subject) is worse than the coarseness it was meant to fix
Outcome: resolved

**Thinking vs. Instruct-mode sampling**: tested whether wopr's llama-swap
config should be changed to Unsloth's documented Instruct (non-thinking)
sampling preset for this checkpoint, via `chat_template_kwargs:
{enable_thinking: false}`. On the same 17-image set, this did not fix
the separate content/reasoning_content routing gotcha (JSON still landed
in `reasoning_content`), gave no latency improvement (avg 7.6s vs. 7.2s),
and produced worse `license_confidence` results on 4/17 diffed images --
notably losing the one catch that mattered most (the watercolor painting
flipped from correctly flagged to `confirmed`).

Decision: keep wopr's default Thinking-mode config as-is; do not add an Instruct-mode llama-swap override
Alternatives-considered: enable_thinking:false + Unsloth's Instruct sampling preset (temp 0.7/top_p 0.80/top_k 20/presence_penalty 1.5)
Rationale: no mechanism or latency benefit, and a measurable quality regression on the license-ambiguity judgment specifically
Outcome: resolved

Separately (mechanical, not a decision, but worth recording as a design
constraint): under Thinking mode, grammar-constrained JSON output can
land entirely in `reasoning_content` with `content` left empty despite a
`stop` finish_reason. `model_client.py`'s `_extract_json` checks both
fields. Found live during the first real image+grammar test run.

Also found, unrelated to any of the above: one real NPS source image
(seen in an earlier unrelated project working with the same source) has
a `.jpg` extension but is actually TIFF-encoded data. PIL's
content-based sniffing handled it fine in the pipeline; logged in
`ROADMAP.md` as a possible future hardening item, not fixed now since
it's a single known instance so far.

## 2026-08-30 -- Validation checkpoint: two real time_of_day bugs found and fixed
[agent-drafted, Josh-approved]

Context: ran the 20-image validation checkpoint (build order step 2) for
the first time against real NPS data. Two of the 22 catalog records'
`time_of_day` values were spot-checked against their actual images and
found wrong, which traced back to two separate, real bugs in the
deterministic evidence pipeline -- not model errors. Both were fixed and
the full checkpoint dataset was reconciled and re-validated against
`schema.json` (26/26 still valid) rather than just patched over.

**Bug 1 -- caption evidence contaminated by park-level boilerplate,
not photo-specific text.** `caption_time_of_day()` was fed a
concatenation of Title + AltText + Description + Keywords
(`nps_client.py`'s `caption_text`). For Minute Man NHP assets, this
included a generic park-history sentence -- "landscapes that witnessed
the dawn of the Revolutionary War" -- repeated verbatim across many
unrelated photos, none of which are actually about dawn. This produced
a confidently wrong "morning" `time_of_day` on 15 of 22 catalog records
in the first pass, none of which involved dawn/morning light at all.
Separately, one caption's photo-credit list contained a person named
"Dawn Marsh," which the same regex also matched as the time-of-day word
"dawn." Fix, two parts: (1) `nps_client.py`'s `caption_text` narrowed to
Title only -- the one field consistently written per-photo, confirmed
by checking that none of the 15 wrong matches came from Title; (2)
`pipeline.py`'s evidence priority flipped to check EXIF before caption
(a real camera timestamp is a hard fact; caption regex matching is a
heuristic), via a new `resolve_time_of_day()` helper with direct test
coverage including a regression test for the exact "Dawn Marsh" case.

**Bug 2 -- EXIF's own `DateTime` tag is file-modified time, not
capture time.** `exif_capture_hour()` read whichever of
`DateTimeOriginal`/`DateTime` it found first from `img.getexif()`'s flat
IFD0 dict, which doesn't include `DateTimeOriginal` at all -- that tag
lives in the Exif SubIFD, reachable only via `exif.get_ifd(ExifTags.IFD.Exif)`.
So the code fell back to IFD0's plain `DateTime`, which is a
last-modified timestamp that editing software freely overwrites on
save. Caught on "Sandhill Cranes in Rosy Morning Light": real capture
time (`DateTimeOriginal`, Exif SubIFD) was 2017-12-03 07:03:21 --
morning, matching the title -- but IFD0's `DateTime` showed a 2025
Photoshop re-save at 22:46:39, producing an incorrect "night" bucket.
Fix: `exif_capture_hour()` now checks `DateTimeOriginal`/
`DateTimeDigitized` in the Exif SubIFD first, falling back to IFD0
`DateTime` only when neither exists. Direct test coverage added,
including the exact re-save-overwrites-original-timestamp scenario.

After both fixes, re-reconciling the same 22-record dataset (no new
model calls needed -- only the deterministic evidence changed) shifted
`time_of_day` on 8 further records and produced a much more plausible
overall distribution (11 afternoon / 8 morning / 3 night, vs. the first
pass's implausible 19/22 "morning"). 18/22 final records are now backed
by a real, correctly-selected EXIF timestamp; the remaining 4 by a
Title-only caption match.

**Bug 3 -- the model's self-reported `time_of_day_evidence` can't be
trusted either.** Found while hand-verifying the reconciled dataset
image-by-image (not from a metrics scan -- this one only showed up by
actually looking): "National Mall & Memorial Parks" had
`time_of_day_evidence: "caption"` even though its title has no
time-of-day word at all and it has no EXIF. `resolve_time_of_day()`'s
fallback branch was returning the model's own self-reported evidence
label verbatim -- but the grammar's enum lets the model emit `"caption"`
or `"exif_timestamp"` even though it only ever receives pixels, and
apparently did so here despite the prompt explicitly instructing it to
use `"visual_inference"`. Fix: the fallback now always returns
`"visual_inference"` unconditionally, ignoring whatever the model
claims -- Claude Code, not the model, decides which evidence source was
actually used, since only Claude Code knows what was actually fed to
it. Changed 3 records' evidence label (not their time_of_day value,
which happened to already match).

Decision: caption evidence restricted to Title only; time-of-day evidence priority is EXIF SubIFD DateTimeOriginal/DateTimeDigitized > IFD0 DateTime > Title caption match > model visual_inference (evidence label always assigned by Claude Code, never taken from the model's own output)
Alternatives-considered: keep full Title+AltText+Description+Keywords for caption matching; keep original caption-before-EXIF priority; ignore the DateTime/DateTimeOriginal distinction; trust the model's self-reported evidence field
Rationale: every alternative here was the literal cause of demonstrated, confidently-wrong output on real data -- this is the checkpoint step doing exactly its intended job
Outcome: resolved

Also, two mechanical resilience fixes landed the same session, prompted
by the checkpoint run itself getting killed by something outside the
process (not a crash) partway through twice in a row: `run()` now
writes a `data/checkpoint.jsonl` line after every candidate (resumable
across interruptions) and caches NPS search results to
`data/candidates_cache.json` (the 28-term search alone took 90-100s,
most of a short run's time budget if redone on every retry). See the
commit history for these -- no `Decision:` trailer, since neither was a
choice between real alternatives, just fixing a real gap the same way
an earlier smoke-test script (used to validate the model choice above)
had already been fixed for the identical failure mode.

## 2026-08-30 -- primary_subject gains "document"; site-inclusion policy set for current sources
[agent-drafted, Josh-approved]

Context: hand-reviewing the checkpoint dataset's images, Josh flagged
`ab8e0d9a...` ("February 1925 Issue of Courier Journal") as a real
photograph of a newspaper page, correctly caught by `is_photograph=true`
but forced into `primary_subject: human_activity` for lack of a better
option -- and noted this collection will keep hitting more of the same
shape (maps, museum display photos, website screenshots) as more
sources are added, distinct from `c1df195b...` (a map *graphic*, already
correctly excluded via `is_photograph=false`).

Decision: add "document" to primary_subject's enum -- for genuine photographs whose subject is a document/informational object (newspaper page, museum placard, interpretive sign, map, screenshot), keeping it distinct from is_photograph=false (non-photographic media, regardless of subject)
Alternatives-considered: force these into human_activity or landscape (the only two enum values a photo-of-a-newspaper could plausibly be squeezed into); add a boolean is_document flag instead of a primary_subject value
Rationale: a plain enum value is consistent with every other primary_subject case and needs no new schema shape; forcing into an existing value was actively misleading (the newspaper's true "activity" content is 1925 news, not anything happening in front of this camera)
Outcome: resolved

Existing checkpoint data was corrected directly (`ab8e0d9a...` ->
`document`) rather than re-run through the model, since the correct
value was already known with certainty from the hand-review; no other
records were affected.

Separately, set the site-inclusion policy for the current NPS-only
dataset (not a schema change -- computed at site-build/filtering time
from `primary_subject`, not stored as a new per-record field):

- `landscape` -- included, this is the collection's actual subject.
- `human_activity`, `document` -- excluded. Neither fits "landscape
  photography."
- `wildlife` -- excluded from the current build, but tracked (already
  is, via `primary_subject` -- no special handling needed) for a
  possible future wildlife-focused addition.
- `structure` -- undecided, tabled. Some famous structures would make
  good wallpapers; most probably wouldn't, and there's no cheap way yet
  to tell the difference automatically. Revisit once there's a real
  need or a plausible heuristic, rather than deciding blind on 1 sample.

Decision: filter the current site build to primary_subject=landscape only; wildlife tracked but held back; structure explicitly deferred, not decided either way
Alternatives-considered: include structure now and sort it out later; drop wildlife from the schema entirely instead of just filtering it
Rationale: landscape is unambiguous; wildlife has a plausible future use so keeping full data costs nothing; structure needs a real "is this one interesting" signal that doesn't exist yet, and one sample (the MIMA farmhouse) isn't enough to design that signal from
Outcome: open-issue -- structure explicitly left undecided, not resolved

## 2026-08-30 -- Kenai Fjords park-scale run: third EXIF bug found and fixed
[agent-drafted, Josh-approved]

Context: first scale-up past the 22-image checkpoint -- 220 candidates
from a single park (`--park` filter added this session), chosen because
it was the largest single-park slice of the existing candidate cache
and a genuine landscape park, to test whether park-concentrated volume
surfaces different problems than the deliberately diverse checkpoint
did. It did: a third real EXIF bug, distinct from both prior ones.

**Bug 3 -- a camera's never-set clock produces a valid-looking but
fake timestamp.** 5 photos (all titled generically "Kenai Fjords
National Park," clearly the same camera/session) had `DateTimeOriginal`
stamped `2000:01:01 00:00:0X` -- not corrupted or unparseable, just a
real value that happens to be the well-known default cameras fall back
to when their clock battery has died or was never configured. Hour 0
bucketed to "night" with full confidence, for photos that (per the
overcast/foggy conditions actually visible in most of them) may or may
not actually be night -- the point is the pipeline had no business
being *confident* either way from this timestamp. The same camera's
IFD0 `DateTime` fallback also produced a plausible-but-wrong value
(`2023:02:17 21:07:57`, almost certainly an upload/processing
touch-date) that would have caused the identical failure a second way
if used as a fallback.

Fix: `exif_capture_hour()` now rejects known sentinel dates
(`_SENTINEL_DATES = {"2000:01:01"}`) rather than accepting them as real
capture evidence, and -- new refinement beyond the first two EXIF
fixes -- a sentinel `DateTimeOriginal`/`DateTimeDigitized` now also
suppresses the IFD0 `DateTime` last-resort fallback, since a sentinel
primary timestamp is a strong signal the same camera's other metadata
is equally untrustworthy. All 5 affected records re-reconciled: their
`time_of_day_evidence` correctly moved to `visual_inference`, and their
`time_of_day` value happened to stay "night" -- checked directly
against the actual images, 4 of 5 are genuinely dark/heavily-overcast
shots where that's a defensible model read, not a repeat of the earlier
bugs' confident-wrong pattern.

Decision: reject known EXIF sentinel dates, and suppress the IFD0 DateTime fallback when the primary SubIFD timestamp is itself a sentinel
Alternatives-considered: only guard DateTimeOriginal/DateTimeDigitized directly and still allow the IFD0 fallback to run (rejected -- demonstrated on this exact data to produce a second wrong answer, not a hypothetical risk)
Rationale: a sentinel timestamp is evidence about the whole camera's metadata reliability, not just one field; falling through to caption/model when no evidence can be trusted beats confidently asserting a coin-flip
Outcome: resolved

Full-dataset stats after this run (246 total: 241 catalog + 5 excluded,
spanning the original 5-park checkpoint batch plus Kenai Fjords):
`primary_subject` landscape 136 / human_activity 64 / wildlife 35 /
vehicle 3 / structure 2 / document 1. `time_of_day_evidence` now 231
exif_timestamp / 8 visual_inference / 2 caption after this fix (was 233/6/2
before it) -- Kenai's photos are heavily EXIF-backed (professional NPS
photography), so the sentinel-date failure mode, while real, affected a
small fraction (5/219) of this batch.

## 2026-08-30 -- Rights-of-depicted-people policy: disclaimer, not per-image vetting
[agent-drafted, Josh-approved]

Context: spot-checking the Kenai license flags surfaced a real, distinct
question from the license-flag calibration itself -- does an
identifiable person appearing in an otherwise public-domain NPS photo
create an actual legal problem for redistributing it? Josh had Claude
(web) research this properly rather than treating my own "my general
understanding" answer as sufficient, and brought back a prepared
`TERMS_OF_USE.md` (reviewed and adopted here) along with the findings
below. The underlying research write-up isn't part of this repo.

The finding: copyright status and a depicted person's right of
publicity/privacy are legally independent. NPS's photos being
uncopyrightable government works settles nothing about a third party's
own rights in their likeness, because NPS never held those rights to
begin with and can't waive what it doesn't own. Separately, though: US
right-of-publicity/privacy law is state-law, non-uniform, and
overwhelmingly keyed to *commercial* use (advertising, merchandise,
implied endorsement) with editorial/documentary/noncommercial use
consistently exempted, reinforced by the First Amendment and (per
*Maloney v. T3Media*, 9th Cir. 2017) by courts treating distribution of
the photographic work itself differently from using someone's likeness
in an ad. Every comparable archive (NPS itself, Library of Congress,
Smithsonian Open Access, Flickr Commons, Wikimedia Commons) handles this
identical situation the same way: a disclaimer stating copyright and
publicity/privacy are separate, no warranty that images are free of
third-party rights, no model releases obtained, and reuser
responsibility -- not per-image legal vetting or takedowns in advance.

Decision: adopt the industry-standard disclaimer/reuser-responsibility posture (TERMS_OF_USE.md + a README "License & Rights" section) rather than building any per-image publicity-rights vetting into the pipeline
Alternatives-considered: exclude/blur every image with an identifiable person regardless of prominence; get a formal legal opinion before doing anything further; do nothing / ignore the question
Rationale: the disclaimer approach is the actual professional norm for this exact situation (every peer archive researched uses it, not stricter vetting), the underlying law overwhelmingly protects noncommercial/editorial use, and building automated publicity-rights vetting would be solving a problem the data doesn't show exists at meaningful scale for a landscape-photo project
Outcome: resolved for the current noncommercial phase -- the research's own staged recommendations flag concrete triggers for revisiting this with a real attorney: monetization (ads, print sales, sponsorships), or curating around specific identifiable individuals rather than landscapes-with-incidental-people

Practical note: this doesn't change the existing `license_confidence`
flagging or the `primary_subject`-based site-inclusion policy -- both
already push the actually-risky case (a person as the clear, prominent,
identifiable subject) toward `human_activity`, which is already excluded
from the current landscape-only site build. The disclaimer covers the
residual case within scope: `landscape` images with an incidental
person `flagged_for_review`, which is exactly the "definable
group"/incidental-presence category the research found to be the
best-protected fact pattern anyway (e.g. California's statutory
crowd/incidental carve-out, which generalizes across the researched
states).

`TERMS_OF_USE.md`'s contact field is still a placeholder pending Josh's
choice of contact method before the repo goes public.

## 2026-08-31 -- primary_subject gains "detail"; two misclassified records fixed
[agent-drafted, Josh-approved]

Context: after the site went live, Josh spot-checked the published
gallery and flagged two records as clearly not landscape: `c63f318d...`
("Bear scat on a trail at Exit Glacier") and `a0e94a5c...` ("Various
moss and lichens cover the trunk of a tree..."). Both are real close-up/
macro photographs -- a pile of bear scat on gravel, moss/lichen/pinecone
texture on a tree trunk -- classified `landscape` by the model, likely
because both have a forest setting and no better enum value existed.
Neither is a scenic composition; the frame is dominated by one small
object with no vista.

Decision: add "detail" to primary_subject's enum, for a close-up/macro shot of a small piece of the environment (moss on bark, scat, gravel, bark texture, a single leaf/pinecone) with no scenic composition, distinct from "landscape" (a scene/vista) and "wildlife" (an animal)
Alternatives-considered: force these into "wildlife" (scat isn't an animal) or leave them in "landscape" and rely on tags alone for filtering; add a separate boolean flag instead of an enum value
Rationale: same reasoning as the "document" addition -- a plain enum value is consistent with every other primary_subject case, and tags alone don't let the site-build filter exclude these the way it already excludes human_activity/document
Outcome: resolved

Both known-bad records were corrected directly to `detail` in both
`data/catalog.json` and `data/checkpoint.jsonl` (the latter so the
correction survives a future pipeline re-run, which rebuilds
catalog.json from the checkpoint) rather than re-run through the model,
since the correct value was already known with certainty from Josh's
hand-review. Site-inclusion policy updated: `detail` is excluded from
the landscape-only site build, same treatment as `human_activity`/
`document`.

Open question, not yet acted on: whether other `detail`-shaped
misclassifications remain undetected in the existing 136-record
landscape set (these two were caught by a partial spot-check, not a
full review) -- worth a full re-classification pass under the updated
prompt if Josh wants that assurance before trusting the rest of the
dataset.

## 2026-08-31 -- 8-park scale-up: unhandled decompression-bomb crash abandons the rest of a run
[agent-drafted, Josh-approved]

Context: scaling from single-park (Kenai Fjords) to an 8-park batch
(Yosemite, Grand Canyon, Yellowstone, Glacier, Zion, Grand Teton,
Acadia, Olympic), searched per-park via `"<park> landscape"`/`"<park>
scenic"` terms rather than the generic scenic-keyword pool -- a plain
park-name text search (tried first) returns mostly non-photo
administrative/planning documents (maps, scenic-analysis reports),
`"<park> landscape"`/`"<park> scenic"` returns a much higher photo
fraction. Mid-run, `judge_image()` raised an uncaught
`PIL.Image.DecompressionBombError` on an oversized source file (a
195-megapixel scenic-analysis map graphic, itself one of the
non-photo documents this search still occasionally surfaces). Because
`run()`'s loop only wrapped `build_record()`'s known
`ModelJudgmentError`, this uncaught exception crashed the entire `uv
run vistarium` process -- abandoning every remaining candidate for
that park's run, not just the one bad image.

Decision: wrap the `build_record()` call in `run()`'s loop in a broad `except Exception`, writing a `processing_error` checkpoint outcome and continuing to the next candidate, same resilience pattern already used for download failures and schema-validation failures
Alternatives-considered: catch `DecompressionBombError` specifically; raise PIL's decompression-bomb limit instead of catching around it; validate image dimensions before calling the model
Rationale: the specific exception type isn't the point -- any unexpected per-image failure (this one, or a future truncated file, corrupt EXIF block, or transient model-call error not already covered) should skip that one candidate, not silently abandon the rest of a multi-hundred-candidate run with no checkpoint trace of what was never attempted
Outcome: resolved -- regression test added (`test_run_survives_unexpected_error_building_one_record`)

Practical note: nothing was lost by the earlier crash -- `checkpoint.jsonl`
only marks a candidate processed after it succeeds or fails cleanly, so
the abandoned candidates simply weren't marked as done and were picked
up again once the run resumed with the fix in place.

## 2026-08-31 -- Scanned prints' EXIF is scan time, not capture time; matted/multi_panel/stereograph now skip EXIF entirely
[agent-drafted, Josh-approved]

Context: Josh, reviewing the fresh 8-park batch, was about to correct a
run of Kenai Fjords photos tagged `night`, then stopped himself --
Alaska's near-24-hour summer daylight means a real midnight timestamp
can still look bright, so `night` isn't automatically wrong just
because a photo looks light. Investigating anyway surfaced a different,
concrete bug: 38 records (mostly Yosemite, frame_type `matted`/
`multi_panel` -- archival scans with a visible mat/border/burned-in
caption, e.g. a 1937 "Miguel Meadows, Yosemite N.P." negative) had
`time_of_day_evidence: exif_timestamp` sourced from the file's EXIF
`DateTimeOriginal` -- except that timestamp was `2017:06:30 01:10:03`,
clearly when the print was *scanned* for digitization, not 1937 when it
was taken. 1:10 AM bucketed a bright daytime photo to `night`. Unlike
the earlier sentinel-date bug (`2000:01:01`, an obviously-fake default),
this EXIF value is realistic-looking and passes every existing sentinel
check -- there's no way to catch it from the timestamp alone.

Decision: resolve_time_of_day() now takes frame_type and skips EXIF entirely (falling through to caption, then visual_inference) whenever frame_type != "full_bleed"
Alternatives-considered: try to detect "scan-like" EXIF patterns (e.g. suspiciously round timestamps); trust EXIF but flag matted/multi_panel/stereograph records for manual review instead of overriding automatically
Rationale: frame_type already tells us, for free, that this file is a scan of a physical print/negative -- for exactly that category, "when was this file's EXIF written" and "when was the photo taken" are different questions by construction, not just occasionally unreliable; no pattern-matching on the timestamp itself can fix that
Outcome: resolved -- regression test added; 38 already-published records corrected directly (re-ran judge_image() for a fresh visual read on each, since the model's original visual guess was discarded, not stored, the first time) rather than guessed at. All 38 moved off `night` (28 -> afternoon, 2 -> evening via caption, remainder afternoon/evening) -- consistent with these being ordinary daytime archival photos, not an argument either way about the Alaska midnight-sun case that started the investigation.

Separate note on the original question: the Alaska midnight-sun concern
itself remains a real, *unfixed* open issue -- `hour_to_bucket()` still
assumes standard mid-latitude day/night hours with no geographic/
seasonal awareness, which is exactly the ROADMAP item "cross-check
EXIF-derived time_of_day against the model's own visual guess" already
flagged as not-yet-built. Investigating this session's report did
surface one real bug (worth fixing), but did not resolve the underlying
concern that prompted it.

## 2026-08-31 -- color_mode added; two deterministic heuristics tried and rejected in favor of a model field
[agent-drafted, Josh-approved]

Context: reviewing the same archival scans, Josh flagged that the
catalog has no way to filter black-and-white photos from color ones --
"just a tag" undersold it; he wants it as real structured metadata for
site sorting, not a free-text tag. First attempt was deterministic (this
looked like a pure pixel-math fact, not a judgment call): mean HSV
saturation over a downsampled image, calibrated against the known
archival-scan batch (~6-22 for confirmed B&W, ~25+ for confirmed color).
It immediately mis-tagged a real photo -- a black-sand Kenai Fjords
beach in flat grey overcast light, genuinely color but so desaturated
it scored *below* several confirmed black-and-white archival scans
(mean 9.2 vs. 9.2-17.6). A second attempt (circular variance of hue
among above-floor-saturation pixels, meant to distinguish a real color
scene's diverse hues from a scanned print's uniform tint/grain noise)
tested worse at scale -- roughly 15/40 false positives and 6/40 false
negatives on a random sample, because scan artifacts and dim real-world
color content both violate the assumption in different, unpredictable
ways.

Decision: make color_mode a model-judged field (added to model_client.py's grammar/prompt) rather than a deterministic computation
Alternatives-considered: keep tuning the pixel-saturation threshold; combine both heuristics with a manual-review middle band; ship the first (mean-saturation) version anyway since it was "mostly right"
Rationale: two independent, reasonable pixel-statistics approaches both failed on real data from this exact dataset (moody/overcast coastal Alaska light, archival scan noise) -- the categories aren't cleanly separable by pixel math here, and a vision-language model handles "is this black-and-white" natively and far more reliably than statistical proxies for it. This is the same reasoning that put time_of_day, license_confidence, and primary_subject on the model side of the split in the first place; color_mode was mis-scoped as deterministic at first, not a case for stretching a deterministic approach further
Outcome: resolved -- color_mode is now the 11th model-judged field, single grammar/prompt addition (no separate API call). The 765 already-published records were backfilled via a full judge_image() call per local image, keeping only the new color_mode value and leaving every other already-reviewed field untouched.

## 2026-09-01 -- aesthetic_score added: "sort by Aesthetic Rating (AI)"
[agent-drafted, Josh-approved]

Context: as more, less-curated sources get added, the ratio of
wallpaper-worthy images to mediocre ones will drop -- Josh wants a
"sort by predicted quality" feature so users can wade through volume
without the site needing to be hand-curated. Proposal: score every
image with LAION's aesthetics predictor v2 (a CLIP-based regression
model trained on human aesthetic ratings, `shunk031/aesthetics-
predictor-v2-sac-logos-ava1-l14-linearMSE`), the same tool NVIDIA's own
NeMo Curator ships for exactly this purpose.

Piloted before committing to a full run (per the lesson from the same
day's color_mode work -- verify before scaling): 43 images spanning the
trickiest cases (archival B&W scans, a heavily desaturated black-sand-
beach photo that fooled both rejected color_mode heuristics) scored in
40s batched on wopr's CPU (0.92s/image). Top-scored image was a genuine
striking Grand Canyon vista; bottom-scored was a stitched panorama with
visible seams, a survey marker card, and a watermark -- the signal
tracks "good wallpaper" well. Full 765-record catalog then scored the
same way (~12 min), published via an artifact showing the real
distribution (range 4.06-6.85, median ~5.15-5.19, roughly bell-shaped)
plus the top/bottom 20 of the 411 published landscapes for visual
validation -- Josh confirmed the results were "stunning."

Decision: add aesthetic_score (float) + aesthetic_method (enum: laion_predictor_v2 | manual_review | pending) as a new field pair, deliberately NOT required in schema.json (populated by a separate post-process stage, vistarium-score-aesthetics, not the main pipeline run); expose it in the site only as a "Sort by: Aesthetic Rating (AI)" dropdown option (default), never as a visible per-photo number, with a one-line disclosure caption under the sort control
Alternatives-considered: fold scoring into model_client.py's single judgment call; display the raw/normalized score on each card; a generic "Recommended" label with disclosure only in an About page; percentile-normalize the stored value instead of storing raw
Rationale: torch/transformers is a genuinely heavy (~1-2GB) dependency and a different stack entirely (CLIP regression, not GBNF-constrained llama.cpp) serving a different purpose (ranking, not structured per-field judgment) -- kept as its own module (aesthetic_score.py) and pyproject.toml optional extra, run on wopr rather than the dev VM (which doesn't have the disk headroom). Raw score is stored rather than percentile because sorting by either produces the identical order within one site build -- percentile would only matter if the number were ever displayed/bucketed, which it deliberately isn't. In-label disclosure ("(AI)" in the dropdown option itself, not buried in an About page) was Josh's own call: upfront at the point of use, not clunky, on the bet that users will try it, see it beats wading through mediocre images unsorted, and not mind
Outcome: resolved. Also added build_site.py's _date_sortable() to normalize the deterministic MM/DD/YYYY date field into a client-sortable ISO string for the "Date taken" sort option (~65% of records have no source date at all, mostly archival scans -- both sorts push nulls last, never crash)

Practical infrastructure note: wopr got a proper `vistarium` package
install (editable, `--extra aesthetic`) in its own venv rather than a
disposable pilot script, so `vistarium-score-aesthetics` is a real,
reusable command there for future scrape batches, not a one-off. wopr
needed `python3.12-venv` installed via passwordless sudo (worked; the
earlier dev-VM blocker on interactive sudo doesn't apply there).

Separately: the dev VM hit 96% disk usage (2.2GB free) partway through
this work, briefly worse (98%, 1.4GB free) after carelessly staging a
900MB tarball locally instead of streaming straight to wopr. Fixed by
deleting that tarball, clearing safe/reversible tool caches (uv, plus
Josh-approved ms-playwright/node-gyp/electron/go-build/pip caches and
an unrelated old project directory), landing at 4.1GB free. No runaway
log was the cause this time (checked systemd journal and searched for
large files) -- just legitimate accumulation (13GB of `data/images/`,
several 400+MB individual source files, other tools' caches) on a
48GB disk with no prior cleanup discipline.

## 2026-09-01 -- Search by NPS's own Categories:Scenic tag, not guessed keywords
[agent-drafted, Josh-approved]

Context: deciding whether to scrape more parks or investigate NPS's
curated photo galleries first, Josh went looking for evidence and found
Acadia's official "Night Skies" gallery (Milky Way/comet/night-sky
photos, e.g. "Venus over Breakneck Pond") -- confirmed live that
searching "Acadia" + "night" (an unambiguous query) surfaces none of
them, since their titles don't contain "night" as text. That's a
structural gap in DEFAULT_TERMS keyword search, not a ranking problem.

Investigating the curated-gallery angle (hand-inspecting the gallery
page's embedded JS) turned up something bigger: NPGallery's advanced
search supports `filter=Units:<code>&filter=Categories:Scenic&filter=
ResourceTypes:Image`, targeting NPS's own per-park content
categorization directly. Same embedded-JSON payload extract_payload()
already parses, same Asset shape asset_to_candidate() already handles
-- confirmed live: Acadia alone has 2,537 Scenic-category images (vs.
whatever a handful of keyword guesses happened to match), Kenai Fjords
has 15,242. Categories facet counts confirm Scenic (308,802 site-wide)
is the right category, distinct from Historic/Museum/Map/etc.

Decision: add nps_client.search_park_scenic(park_code) as the preferred search strategy (Units:<code> + Categories:Scenic + ResourceTypes:Image), with fetch_unit_codes() to resolve a park's 4-letter code from its display name via the same "Units" filter facet (683 units, one HTTP request, no separate API/key needed); keep DEFAULT_TERMS/search_candidates for ad hoc cross-park term search, not removed
Alternatives-considered: pursue the curated-album path instead (nps.gov/media/photo/gallery.htm's hardcoded per-park albumIDs + /api/album/metadata) -- still a good secondary idea (NPS park staff's own picks, a further-curated subset), but Categories:Scenic is more foundational: it covers all NPS scenic photography per park, not just the albums someone happened to hand-build a page for
Rationale: this isn't really a new feature, it's fixing the scraper's core search strategy -- every future scrape benefits, so it belongs before scraping more parks with the weaker method, not after
Outcome: resolved. Two real bugs found and fixed live before trusting this at scale:
1. asset_to_candidate() always trusted NPSUnits[0] for the `park` field. A shared historical asset can be cross-tagged under several NPS units at once (found live: one Grand Teton search hit was tagged under Devils Tower, Grand Canyon, Grand Teton, AND the Museum Management Program simultaneously) -- units[0] was Devils Tower, silently mislabeling a Grand Teton candidate. Fixed by threading park_code through to prefer the NPSUnits entry matching the unit actually searched for, falling back to units[0] only when no park_code is given (generic keyword search) or none matches.
2. DEFAULT_MAX_PAGES_PER_PARK (20, ~10,000 candidates) would have silently truncated Kenai Fjords' real 15,242-candidate pool by a third, and NPS's own default result order isn't random -- raised to 200 pages (a backstop, not a target; fetching search-result pages is cheap, no model calls involved).

Separately: pipeline.run() previously took new candidates via a
positional `[:limit]` slice. With per-park pools now in the thousands
(vs. hundreds under keyword search), that would bias every run toward
whatever NPS's own default sort puts first, not a representative sample
of the park's photography. Replaced with _sample_candidates(), a real
random.sample() over the unprocessed pool.

Practical note: search only builds a candidate *list* (cheap, metadata-
only HTTP calls); nothing downloads or runs through the judgment model
until pipeline.run()'s --limit lets it through, unchanged. At the
model's real per-image rate (~10-20s observed this session), processing
Kenai Fjords' full 15,242-candidate pool alone would take ~2.6 days
continuous, and the site-wide 308,802-image Scenic category ~53 days --
confirming --limit + random sampling per run is the permanent strategy
here, not a stopgap.

## 2026-09-01 -- Curated albums as the primary content strategy, Categories:Scenic as the smoke-test tier
[agent-drafted, Josh-approved]

Context: with search_park_scenic() working, the next question was
strategy -- keep scraping more parks under Categories:Scenic (still
NPS's own categorization, but automated/uncurated at the per-photo
level), or go after NPGallery's hand-curated albums first (the ones
`list_park_albums()`/`search_album()` were built for the same day).
Josh's call: albums are already curated, so they're the higher-payoff
strategy; Categories:Scenic random sampling stays useful as a smoke
test, not the main approach.

Confirmed the album-selection problem is real, not hypothetical:
Acadia alone has 211 albums, spanning genuine scenic collections
("Cadillac Mountain," "Acadia's Night Skies," "Sand Beach to Otter
Point") and administrative/historical ones in roughly equal measure
("Acadia Awards Gathering 2025," 1930s George B. Dorr correspondence/
receipts, ADA-accessibility "Access: ___" documentation for every
parking lot and picnic area in the park). Title/description alone is
readable well enough for a human (or Claude, reviewing the list) to
sort landscape-worthy albums from administrative ones, but there's no
reliable *algorithmic* signal to automate the split -- "Duck Brook
Bridge" and "Eagle Lake Boat Ramp Parking" read identically to a
keyword filter despite one being scenic and the other a parking-lot
photo survey.

Decision: hand-review each park's album list (title/description) to pick a landscape-worthy shortlist, then scrape exactly those via search_album() -- not an automated album-selection heuristic
Alternatives-considered: score every album's own thumbnail/description with the aesthetic predictor or the judgment model to auto-select; keyword-filter album titles (e.g. reject anything containing "Access:" or "Meeting")
Rationale: a title-keyword filter would still misfire (many genuinely scenic albums have plain place-name titles indistinguishable from administrative ones without reading the description closely), and scoring 211 albums' worth of thumbnails to pick ~15 worth 500 real images is more model-call overhead than just reading 211 short lines once per park -- this is a case where cheap human/Claude judgment beats building a classifier for a one-time, per-park decision
Outcome: resolved for Acadia -- 17 albums picked from its 211 (Night Skies, Seasons, Cadillac Mountain, Baker Island, Sand Beach to Otter Point, Schoodic Peninsula, Acadia's Summits, Best of Acadia, Views of Acadia, Winter Storms Jan 2024, Acadia's Lighthouses, Acadia's Geologic Features, Jordan Pond + Jordan Pond Path, and 2 general "Acadia National Park" collections including one from a dedicated NPS volunteer photographer), 470 candidates total. A 10-image smoke test (per this session's own established practice) ran clean before committing to the full batch: 7/10 landscape, 1 structure, 2 detail, zero wildlife/document/human_activity, zero park-misattribution -- a markedly better hit rate than either keyword search or the broader Categories:Scenic pool. Full ~460-image remainder launched in the background afterward.

A medium-confidence second tier was also identified but not yet run:
the 8 "Carriage Roads - [Loop]" albums and 3 "Hike ___" trail-photo
albums (~200 more images) -- no "Access:" caveat and plausibly scenic,
but less certain than the top tier from description alone.

## 2026-09-01 -- dominant_color added mid-scrape; same lesson as color_mode, this time built ASAP on purpose
[agent-drafted, Josh-approved]

Context: ROADMAP had flagged a "dominant/overall color" filter (blue,
green, white, etc. -- distinct from color_mode) as maybe-deterministic,
maybe-model, unresolved. Josh, mid-Acadia-album-scrape, asked the right
question before more corpus got added: if this ends up needing the
model, it should go into the grammar *now*, not after -- every image
scraped before the field exists is one more image that needs a
backfill later. Same principle as the color_mode lesson from the day
before, applied proactively this time instead of discovered by
shipping the wrong version first.

Tested deterministic first, fast: a pixel-majority dominant-hue bucket
(HSV histogram over saturation/value-filtered pixels, achromatic
fallback for white/gray/black). Failed immediately on a real image --
a red-rock Grand Canyon photo with a large blue sky above it scored
"cyan," because the sky's uniform saturated pixels outvoted the darker
(shadowed, but visually dominant/subject) canyon rock on pure pixel
count. Same root cause as color_mode's rejected heuristics: pixel-area
dominance isn't the same question as perceptual/compositional dominant
color, which is what "what color is this photo" actually means to a
person.

Decision: add dominant_color (red | orange | yellow | green | blue | purple | white | gray | black) to model_client.py's grammar/prompt as a model field, NOT deterministic
Alternatives-considered: keep tuning the pixel-histogram heuristic (weight by inverse distance from center, subject-detection first); defer the decision until after the current scrape batch finished
Rationale: same as color_mode -- this needs perceptual/compositional judgment a VLM handles natively, not a pixel-counting proxy for it; deferring would have let the in-flight ~450-image Acadia batch (and any further scraping) accumulate without the field, growing the eventual backfill
Outcome: resolved -- added to the grammar immediately, smoke-tested against 4 real images including the failing Grand Canyon case (now correctly "red") and the color_mode edge cases (black sand beach -> "gray", archival B&W -> "gray", a blue-dominant sunset/water shot -> "blue", all checked against the actual images). Deliberately NOT added to schema.json's required list yet: the Acadia album scrape was already running when this landed, using model_client.py's prior grammar already loaded in that process's memory -- marking it required would have made every remaining candidate in that live run fail schema validation. Will promote to required once a backfill pass covers the whole corpus, including whatever this in-flight batch adds without it.

## 2026-09-01 -- Acadia album batch results; 360-degree panoramas excluded from the site
[agent-drafted, Josh-approved]

Context: the 17-album, 460-candidate Acadia batch (see the curated-
albums entry above) finished: 1235 total catalog records (up from 775),
308/460 (67%) landscape -- a markedly better hit rate than any prior
strategy (keyword search, Categories:Scenic random sampling). 459/460
correctly attributed to Acadia National Park (one outlier, a
Geologic Resources Division cross-tag, not investigated further --
negligible).

Reviewing the batch, several titles contained "360" ("Acadia National
Park (360 photo)", "360 degree view from Bass Harbor Head Light",
"Grand Canyon Lodge Sun Room - 360 Panorama" from an earlier batch).
Checked one: a genuine 2:1-aspect equirectangular panorama with visible
barrel distortion (curved rock ledges, warped foreground) and a tripod/
camera in frame. No crop_anchor/crop box can fix that -- cropping an
equirectangular projection to 16:9 just shows a slice of the same
distortion, not a corrected rectilinear view. Distinct from genuine
wide-format panoramic photography (e.g. the earlier-found "Wood's
Ridge"/"Smith Peak" 1937 fire-lookout panoramas, also ~2:1 aspect but
optically flat, no distortion, crops fine) -- aspect ratio alone isn't
the right signal, but NPS's own titling convention ("360 photo/image/
degrees/Panorama") reliably distinguishes the two, confirmed against
all cases found in the corpus so far.

Decision: exclude records whose title contains "360" from the published site (build_site.py's _is_360_panorama()), alongside the existing primary_subject:landscape filter
Alternatives-considered: add a model field for panorama/projection type (another grammar change + backfill, same day as two others); attempt actual equirectangular-to-rectilinear reprojection (real engineering effort for a handful of images); leave them in and accept the distortion
Rationale: purely deterministic, zero model cost, and the title convention has been 100% reliable on every case found so far -- no need for a model judgment call or new field when a cheap, accurate signal already exists in data already being scraped
Outcome: resolved -- regression test added (test_is_360_panorama_detects_nps_titling_conventions, test_build_site_excludes_360_panoramas)

## 2026-09-01 -- Curated-scale pipeline: threshold-with-floor selection, album keyword triage, CC BY metadata
[agent-drafted, Josh-approved]

Context: discovering NPGallery's search/album APIs this session revealed
real scale -- Categories:Scenic alone returns 308,802 images site-wide,
and Acadia's albums alone number 211. Processing all of that through the
per-image VLM judgment call, the project's working model until now, was
designed for hundreds of candidates, not hundreds of thousands, and most
of that volume isn't wallpaper-worthy regardless. Josh's response: make
Vistarium a curated selection gated by aesthetic score before the VLM
ever runs, not an exhaustive catalog.

Decision: reorder the pipeline for future scraping -- album-keyword triage (metadata only, no image bytes) -> thumbnail fetch (ProxyLoRes, ~78KB vs. Original's 1-2MB+) -> aesthetic pre-scoring (batched, GPU) -> threshold-with-floor selection (keep score >= threshold per park, but top up to a minimum floor -- e.g. 10 -- per park so no park is excluded outright for being less photogenic on average) -> only survivors get full-res download + VLM judgment (unchanged)
Alternatives-considered: fixed N-per-park instead of threshold-with-floor (doesn't adapt as corpus/threshold understanding evolves); score at full resolution instead of thumbnails (unnecessary bandwidth -- the aesthetics predictor's CLIP backbone doesn't need full-res input); drop parks that don't clear threshold entirely (rejected -- the floor exists specifically so no park gets zero-ed out just for having a lower average score)
Rationale: this is genuinely a different pipeline shape than "search returns a few hundred candidates, judge them all" -- pre-filtering on a cheap batched signal before the expensive per-image VLM call is the only way this scales to real NPGallery volume
Outcome: resolved. New modules: album_triage.py (classify_album() -- exclude/include/ambiguous from a versioned album_keywords.json; ambiguous falls through rather than being dropped, since there's no reliable way to automate telling a landscape-worthy album from an administrative one by title alone -- Acadia's 211 albums split roughly evenly), curate.py (select_by_threshold_with_floor(), select_candidates_for_park() orchestrating the full chain). nps_client.py gains download_thumbnail() (confirmed live: GetAsset/<id>/proxy/lores serves the ProxyLoRes derivative already advertised in album API responses) and a shared request throttle (see below). aesthetic_score.py gains CUDA auto-detection (falls back to CPU transparently) and a renamed aesthetic_method value (aesthetics_predictor_v2_l14_linearMSE, more specific than the prior laion_predictor_v2 -- both stay valid in schema.json's enum, existing records untouched). pipeline.py wires it in via --curate-park-code/--threshold/--floor/--keywords, additive to the existing --album-id/--park-code/--term strategies, none of which were removed. threshold has no hardcoded default anywhere it's used -- Josh was explicit this is a moving target pending real score-distribution data, not something to guess at.

Existing 1235-record corpus (scraped before this pipeline existed) is
left as-is -- this changes how future scraping works, not a retroactive
re-filter of what's already published.

Request throttling: added mid-implementation after Josh found NPS's
*other* public API (developer.nps.gov, API-key gated) documents 1000
requests/hour as its default rate limit. npgallery.nps.gov (what this
project actually talks to) has no published limit of its own, but that's
the closest signal available for what NPS considers reasonable automated
access -- used as the anchor. Every NPGallery request (search, album
listing, full-res download, thumbnail fetch alike) now funnels through
one throttled choke point (nps_client._http_request(), ~3600 req/hour
ceiling, thread-safe across ThreadPoolExecutor workers) rather than
firing as fast as concurrency allowed.

## 2026-09-01 -- Metadata license: CC0 -> CC BY 4.0, whole-record not per-field
[agent-drafted, Josh-approved]

Context: building the curated-scale pipeline above surfaced a second
question -- a curated, aesthetically-scored, triaged selection is real
editorial/compilation work, which changes what license fits Vistarium's
own catalog data. The CC0 public-domain dedication (in place since
2026-08-31) understates that; LICENSE-DATA also carved out "catalog
fields pulled unedited from a source institution's own API" from the
dedication, a field-by-field split Josh wants removed.

Decision: LICENSE-DATA and README.md's "License & Rights" section now license CC BY 4.0, applied to each catalog record as a compiled whole -- no field-by-field split between sourced vs. Vistarium-generated fields
Alternatives-considered: keep CC0 but drop only the per-field carve-out; keep the field-level split but change CC0 to CC BY; a separate license per field category
Rationale: individual descriptive fields (time_of_day, tags) carry thin-to-no independent copyright on their own -- the actual unit of authorship is the compiled, curated, scored index itself (which images were selected, how they're described), which is what compilation copyright protects and what CC BY should therefore cover as a whole, not piecemeal
Outcome: resolved. Images remain entirely outside both licenses, unchanged -- that split (images vs. metadata) is a real difference in asset class and ownership, not the kind of per-field split being removed here.

## 2026-09-01 -- GPU aesthetic-scoring benchmark: inference is free, preprocessing is the bottleneck, the NPS throttle is the real ceiling
[agent-drafted, Josh-approved]

Context: before scaling curate.py up to real volume, benchmarked
aesthetic_score.py's real throughput on wopr's GPU (CUDA-enabled torch
installed there for this; the pilot venv had been CPU-only), per
Josh's request. 150 real Acadia thumbnails (not synthetic -- real
JPEG decode/preprocessing behaves differently), batch size sweep
[1, 8, 16, 32, 64], scripts/benchmark_aesthetic.py.

Results: GPU inference time is ~flat regardless of batch size (~12ms/
batch whether scoring 1 image or 64) -- the CLIP forward pass is
essentially free on this hardware. The real cost is CPU-side
preprocessing (PIL decode + CLIPProcessor's resize/normalize), which
scales ~linearly with batch size (11ms/image at batch=1, ~11.7ms/image
at batch=64 -- nearly identical per-image cost). Throughput still rises
with batch size (45 img/s at batch=1 -> 84 img/s at batch=64) purely
from amortizing fixed per-call/Python-loop overhead across more images,
not from any real preprocessing speedup. Cold start (model load + first
batch): 7.68s, one-time.

Decision: set aesthetic_score.py's default BATCH_SIZE to 64 (best throughput observed; diminishing but still real returns past 32)
Outcome: resolved. At ~85 img/s, GPU-batched scoring makes even huge candidate pools cheap in absolute terms -- Kenai Fjords' full 15,242-image Categories:Scenic pool would score in ~3 minutes, versus ~4 hours at the CPU pilot's ~1 img/s. But this surfaces a different, previously-invisible bottleneck: nps_client's request throttle (1000 req/hour, one request per thumbnail fetched) means fetching that same 15,242-thumbnail pool alone would take ~15 hours, regardless of how fast scoring is. Scoring throughput is no longer the constraint on how much of NPGallery this project can practically curate -- bandwidth/throttle is. Worth weighing directly when picking how many parks/candidates to curate next, not something to revisit later as a surprise.

## 2026-09-02: dev VM disk-space incident; site build moved to wopr

Context: `scripts/sync_and_publish.py` (the periodic loop publishing the
curated scrape's results) was rsyncing wopr's `data/images` (all
full-res originals, growing continuously as the 61-park scrape ran)
to the local dev VM before running `vistarium-build-site` locally.
The dev VM only had ~1GB free to begin with (already tight from the
`aesthetic` extra's install failure earlier this session -- see the
GPU-benchmark entry above). Acadia alone added ~17GB of full-res
images; the dev VM disk hit 100% mid-rsync at 2026-09-02 00:45, and
every sync cycle failed silently after that (`No space left on
device`) until caught by manual inspection ~2 hours later. The scrape
on wopr was unaffected (its own disk has 167GB free) -- only the
local publish side broke.

Decision: the dev VM never needs full-res images at all.
`build_site.py` now runs entirely on wopr (via ssh, wopr's
aesthetic-pilot venv) against wopr's own `data/catalog.json` +
`data/images`; only its small output -- `docs/data.json` and
`docs/thumbs/*.webp` -- gets rsynced back. `docs/index.html`,
`app.js`, and `style.css` (the actual hand-authored site source)
live in the local git checkout as always and get pushed *to* wopr
once so its build has a current copy to render against.
Outcome: resolved. Local `data/images` (17GB, redundant with wopr's
copy) deleted to recover disk space; verified end-to-end (752 records
built on wopr, only `docs/` synced locally, committed and pushed)
before restarting the loop.

## 2026-09-02: album_keywords.json exclude-list expansion (round 2)

Context: Josh reviewed the full 3,314-album triage list (published as
an artifact) and proposed ~30 new exclude terms across arts/
competitions, events/programs, infrastructure/maintenance,
administrative/staff/documentation, and fauna/wildlife categories.

Checked each proposed term against all 2,990 not-currently-excluded
albums before adding (title + description substring match, matching
`classify_album`'s actual matching behavior) to catch false positives.
Found two: bare `"sign"` matches `"design"` ("Design renderings of
future Chisos Mountains Lodge building"); bare `"sar"` (for "search
and rescue") matches `"anniversary"` ("175th Anniversary
Celebration"). Fixed by using `" sign"` (leading space -- catches
"Sign"/"Signs" after a space or start of string, not "design") and
dropping the bare `"sar"` abbreviation entirely (redundant with the
already-safe full-phrase `"search and rescue"`). Every other proposed
term checked clean against the full corpus.

Effect measured against the full 3,314-album snapshot: exclude count
324 -> 893 albums, keeping 13,282 additional images out of the
thumbnail-fetch/scoring pipeline entirely.

Scope note: this only affects parks the curated scrape hasn't reached
yet. Acadia (park 1) and Arches (park 2, in-flight) already ran their
album triage under the old keyword list before this update landed on
wopr -- their results are not retroactively reclassified. Re-running
them under the new list was considered and rejected: it would waste
already-spent NPS throttle time and already-scored/tagged work for a
purely incremental precision gain, not a correctness bug.

## 2026-09-02: producer/consumer pipeline for the curated scrape (CPU scoring, GPU-exclusive tagging)

Context: the curated scrape ran strictly sequentially per park --
album triage, thumbnail fetch, aesthetic score, select, then full-res
download + VLM tag, one park fully finishing before the next one's
crawl began. GPU (wopr's qwen VLM, via llama-swap) sat idle for the
vast majority of each park's wall-clock time (~88 min of thumbnail
fetching for Acadia, vs. ~17s of GPU scoring and ~31 min of GPU
tagging). Back-of-envelope estimate: overlapping GPU tag time for
park N with network/CPU work for park N+1 onward would save roughly
28 hours off the remaining ~95-hour run (~29%) -- see conversation,
2026-09-02. Josh: "Yes, I think it's worth it, let's do it."

Considered running aesthetic scoring on GPU concurrently with VLM
tagging (both are GPU work, seemingly the obvious pairing) but
rejected: wopr's GPU already has ~26.5GB of 32GB resident to the qwen
VLM model via llama-swap, leaving only ~6GB headroom. That's enough
for CLIP *at rest* (confirmed in the earlier GPU-scoring benchmark),
but running a second CUDA context alongside Qwen *actively serving
tag requests* risked eviction/reload thrashing (llama-swap's whole
job is swapping models in and out of VRAM on demand) -- a cost that
could easily exceed whatever concurrency would have saved.

Decision: force aesthetic scoring to CPU instead
(`aesthetic_score.set_device("cpu")`), keeping wopr's GPU exclusively
dedicated to VLM tagging. This costs nothing once pipelined: CPU
scoring runs at ~1 img/s, well ahead of the NPS throttle's ~0.28
img/s thumbnail-arrival rate, so it never becomes the bottleneck.

Implementation: `scripts/run_curated_scrape_remote.py` rewritten as a
producer/consumer pair on a shared `queue.Queue`, not per-park
sequential calls to `pipeline.run()`. Producer thread: album triage +
thumbnail fetch + CPU aesthetic scoring + threshold-with-floor
selection, park by park, pushing each park's selected candidates
(plus a `_PARK_DONE` sentinel) onto the queue as soon as that park's
selection is final. Consumer (main thread): pulls off the queue and
does full-res download + VLM tag, decoupled from which park the
producer is currently working on -- reuses `pipeline.build_record()`,
`_load_checkpoint()`, `_write_checkpoint_line()` directly rather than
duplicating that logic. A park is marked done in
`curated_scrape_progress.json` only when the consumer finishes
tagging everything queued for it (not when the producer finishes
selecting), preserving the existing resume semantics.

`nps_client`'s request throttle (`_rate_lock`, a `threading.Lock`)
was already thread-safe -- both the producer's thumbnail/album
requests and the consumer's full-res downloads correctly serialize
through it without additional locking.

## 2026-09-02: wopr disk filled by a stuck logrotate deadlock (llama-swap.log)

Context: wopr's root disk hit 100% full mid-scrape (Denali, park
16/61), causing intermittent thumbnail-fetch failures and a failed
site-build cycle. Not caused by vistarium's own data (`data/images`
23GB + `data/thumbs_cache` 957MB, nowhere near the disk's 455GB) --
`/home/josh/logs/llama-swap.log` (llama-swap's own request log, ~155GB
and actively growing) was the actual cause. Likely driven by our own
VLM tagging load (thousands of requests/day hitting llama-swap) hitting
a log that was never rotating.

Root cause, not just "the log grew too big": an existing logrotate
config (`/etc/logrotate.d/llama-swap`, `maxsize 500M`, `copytruncate`,
`weekly`) had been silently deadlocked since 2026-08-30.
`copytruncate` copies the log to a `.1` file before truncating the
original -- that copy needs free disk space equal to the log's current
size. The very first rotation attempt (Aug 30) already failed with
"No space left on device" while making that copy; when the copy step
fails, logrotate correctly refuses to truncate the original (would
lose data with no successful backup), so the file was never reset and
kept growing -- meaning every subsequent day's attempt needed even
more headroom than the last, permanently. The daily
`journalctl -u logrotate.service` history showed later runs exiting
"successfully," which was misleading -- other logrotate.d stanzas
processed fine each day while this one stanza kept silently failing to
even attempt rotation (its size never dropped). Also relevant: the
system logrotate.timer only fires once daily, so even a healthy
`maxsize` trigger has up to 24 hours to blow past the threshold under
heavy load before the next check.

Fix:
- Immediately truncated `llama-swap.log` directly (`sudo truncate -s 0`
  -- safe on an actively-open file, doesn't require llama-swap to
  restart or reopen its handle) to free the 155GB right away, since
  copytruncate itself had no room to run.
- Lowered `/etc/logrotate.d/llama-swap`'s `maxsize` from 500M to 100M
  and switched `weekly` -> `daily` (daily is now just the fallback
  baseline; maxsize is what actually matters under load).
- Added a dedicated systemd timer (`llama-swap-logrotate.timer` +
  `.service`, `/etc/systemd/system/`) running only this one
  logrotate.d stanza every 15 minutes, independent of the system's
  once-daily `logrotate.timer` -- keeps the log too small for a
  copytruncate deadlock to ever recur, and doesn't touch the cadence
  of any other logrotate.d config on the box.
Outcome: resolved and verified -- `logrotate --debug` confirms the
100M threshold is recognized, the new timer's service exits 0, wopr's
scrape resumed thumbnail fetching within seconds of the truncate (its
existing per-candidate try/except already handled the failures
gracefully, no restart needed), and the next sync_and_publish.py cycle
published successfully. This is infrastructure outside the vistarium
project itself (wopr is Josh's own GPU box, shared with other
services) -- flagged before acting, truncated and reconfigured with
his explicit go-ahead, not unilaterally.

## 2026-09-02: persist every scored candidate, not just the selected ones

Context: `curate.select_candidates_for_park()` computed an aesthetic
score for every candidate in a park's pool, but only ever returned
(and therefore only ever persisted, via checkpoint.jsonl/catalog.json)
the ones that cleared threshold-with-floor selection. Below-threshold
scores were computed once for the selection decision and then
discarded -- thumbnails stay cached (`data/thumbs_cache/`, never
deleted), but the score itself, and which candidate it belonged to,
was gone.

Josh: keep this data, for three reasons -- adjusting the aesthetic
threshold after the fact without rescanning NPS, corpus-wide stats,
and reuse by a separate wildlife-photo pipeline off the same scan
(project-kickoff.md's schema already has `primary_subject: wildlife`
as a category; a landscape-low-scoring image can still be a great
wildlife shot, and the current pipeline never even VLM-tags it to
find out, since VLM tagging is gated behind landscape-aesthetic
selection).

Decision: `select_candidates_for_park()` now writes every scored
candidate (full NPSCandidate fields + aesthetic_score, selected or
not) to `<workdir>/scored_candidates/<PARK_CODE>.json` before
selection happens. Gitignored (`data/`), local-only, same as
thumbs_cache. Only affects parks scored after this deploys -- Acadia
through Denali (the parks already processed by the time this landed)
don't have a manifest. Backfilling them would mean re-hitting NPS's
album API to reconstruct each candidate's metadata (search_album()
results were never cached, only the resulting thumbnail files),
though re-scoring itself would be free (thumbnails already local) --
not done here since it wasn't asked for, flagged as a follow-up if
wanted.

## 2026-09-02: aesthetic_score wasn't reaching curated-scrape catalog records; site now gates on it

Found while implementing Josh's request to filter the site down to
records currently meeting the aesthetic cutoff without deleting
anything: checked the live site's actual score distribution first and
found 790 of 1,512 published records had no aesthetic_score at all.
Traced to wopr's real catalog: 1,461 of 2,696 records were missing
it -- every single record produced by the curated scrape since it
started, with zero exceptions.

Root cause: `curate.select_by_threshold_with_floor()` used a
candidate's score to *decide* selection, then returned the bare
`NPSCandidate` -- which had no field to carry a score on in the first
place. `pipeline.build_record()` builds the catalog record purely
from `NPSCandidate` fields + the VLM's judgment fields; the score was
never in either, so it was silently absent from every curated-scrape
record. The 1,235 pre-curated-pipeline records all have scores because
they went through a separate one-time `aesthetic_score.backfill()`
pass, unrelated to this code path -- that's what masked the bug this
long. Also unrelated: `aesthetic_score` was never added to
`schema.json`'s `required` list (unlike `dominant_color`), so
`schema_validate.validate_record()` had nothing to catch this either.

Fix:
- `NPSCandidate` gained `aesthetic_score` / `aesthetic_method` fields
  (default `None` -- only the curated path ever sets them).
- `select_by_threshold_with_floor()` now returns candidates via
  `dataclasses.replace(c, aesthetic_score=s, aesthetic_method=...)`
  instead of the bare originals.
- `build_record()` writes `aesthetic_score`/`aesthetic_method` into the
  record when the candidate carries one (uncurated search paths --
  `--term`, `--park-code`, `--album-id` -- still produce records with
  neither key, same as always).
- `build_site.py` gained `PUBLISH_MIN_AESTHETIC_SCORE = 5.4` (matching
  the scrape's own threshold) as a *display* gate: `build_site()`
  drops any record with no score or a score below it from
  `docs/data.json`, but nothing is removed from `data/catalog.json` --
  a record reappears automatically once it's rescored or the threshold
  changes. This was Josh's actual ask ("filter out any images... don't
  delete them, just don't show them right now").

Deploy: fixed code synced to wopr and the scrape process restarted
(picks up cleanly -- 15 already-done parks skipped, Denali reselected
from cached thumbnails, no data loss). Existing catalog records
missing a score need a one-time `aesthetic_score.backfill()` pass
against their already-downloaded full-res originals to catch up
(re-scores from `data/images/*.jpg`, not the original thumbnail used
for selection -- expected to be very close but not bit-identical).

## 2026-09-02: Denali failure traced to a corrupted thumbnail; two real bugs fixed

Context: Josh noticed STATUS.md showing Denali as "in progress" while
Glacier and Glacier Bay (actually done/in-progress) showed as not
started. Traced to two compounding bugs, not one.

Bug 1 (the actual scrape failure): a thumbnail
(`2a73d13c-d0c5-4172-b894-099556d2a984.jpg`) was left truncated
mid-write by the wopr disk-full incident earlier the same day.
`download_thumbnail()`'s idempotent "skip if file exists" check meant
it was never re-fetched. `aesthetic_score.score_batch()` called
`Image.open()` unguarded inside a list comprehension building the
whole batch -- one corrupt file raised `UnidentifiedImageError` and
aborted scoring for Denali's entire 4,454-candidate pool, every single
time it was attempted (confirmed via the traceback in
curated_scrape.log: `producer: DENA (Denali) selection failed,
skipping park`). Found and deleted 285 similarly-corrupted thumbnails
across the whole cache (all under 1KB, all from the same incident
window) -- any of the still-pending parks could have hit the same
failure on a different file.

Fix: `score_batch()` now opens each image individually inside a
try/except, returning `None` for any that fail (logged as a warning)
instead of raising -- `list[float]` became `list[float | None]`,
aligned 1:1 with input paths same as before. `score_all()` already
naturally skips `None` entries. A bad image now costs one candidate,
not an entire park.

Bug 2 (the status-page symptom): `_write_status_md()`'s "in progress"
indicator assumed parks complete in `national_parks.json`'s fixed
order -- "the first not-done park in list order" -- which is exactly
wrong once any park fails outright and gets skipped rather than
completed (Denali, from Bug 1): every status check from then on
showed the abandoned park as perpetually "in progress" while parks
that had genuinely finished or were genuinely in progress after it
showed as not-started. Fixed by reading `curated_scrape.log`'s most
recent "selecting candidates" line directly (ground truth for what
the producer is actually doing right now) instead of inferring from
completion order.

Outcome: both fixes deployed and smoke-tested against the live wopr
log before rollout. Denali will be retried cleanly on the current
run's next pass now that its corrupted thumbnail is gone.

## 2026-09-02: expose image_url (the direct full-res link) in docs/data.json

`data/catalog.json` has always carried `image_url` (the direct
download link to the full-res original, e.g. `.../GetAsset/<id>/
Original`) on every record, but `build_site.py`'s exported
`docs/data.json` -- the metadata anyone actually reads, whether
browsing the site or scripting against it -- only ever included
`source_url` (the NPGallery *page* for that asset, not a direct image
link). Josh: "I don't have to find them after the fact if I want to
use them in scripts." Fixed: `build_site()` now includes `image_url`
in every published record.

## 2026-09-03: known gap -- exact-hash dedup misses reprocessed re-uploads

Josh spotted a real duplicate live on the site: two Denali records
(`7cf4ae2b...` "Scenic View" and `2bfd2a25...` "Who Goes There?") are
the same marmot-in-valley photo, uploaded to NPGallery twice with
different processing (different sharpening/color grading -- different
file hash, different size, same shot). Both cleared the aesthetic
threshold and both got tagged `landscape`, so both published.

The pipeline's only dedup is exact byte-hash matching (the source of
every "duplicate of X, skipping" log line seen throughout this scrape)
-- it only catches byte-identical files. A photo re-exported with
different settings and uploaded under a second ID evades it entirely,
and there's no reason to think this pair is unique; any similarly
reprocessed re-upload across any park would slip through the same way.

Decision: don't touch the running 61-park scrape to fix this now --
noted here as a known limitation instead. Options for later (not
decided): perceptual hashing (pHash/dHash) to catch near-duplicates,
or a one-off manual sweep after the full run completes. Flagged as a
follow-up, not fixed.

Update, same day: Josh found a second, larger cluster while manually
scanning Denali -- `82332331...`, `e885fa3a...`, `25e8aed3...`,
`e4782404...` are four frames of the same Wonder Lake/Denali sunset
burst (same framing, clouds/light shifting between frames), all four
published. He also flagged `2b8a099d...` from the same shoot as
*not* a duplicate -- confirmed visually: same session, but a distinctly
tighter crop on the mountain, a genuinely different composition.

This pair -- 4 near-identical frames vs. 1 real near-neighbor that
should stay -- is a ready-made calibration set for whatever perceptual-
hash approach eventually gets built: the burst should cluster at a very
small hash distance, `2b8a099d...` should land clearly further out.
Recorded here rather than acted on now, same reasoning as above (CPU-
only work like pHash is cheap and could run once the producer finishes
all 61 parks' selection and its CPU frees up, without competing with
the GPU-bound tagging consumer -- discussed but not started).

Update, same day: a third find widens the problem past pixel-level
duplication. `7b2b2a67...` and `8a459367...` are the same Denali park
road switchback, same camera vantage point, but genuinely different
photos -- a different tour bus (green vs. cream), different position on
the road, different pass/day. Josh's read: "even if they aren't the
same, it's not worth keeping all of them" -- redundant *composition*,
not redundant pixels.

This case wouldn't be caught by pixel-hash or perceptual-hash dedup
(pHash/dHash) at all -- the actual pixel content differs too much.
Catching it needs similarity at the composition/embedding level
instead, e.g. cosine distance between CLIP embeddings -- notably, the
aesthetic scorer's CLIP backbone already computes an embedding per
image as a side effect of scoring, so this could reuse work already
being done rather than requiring a second pass. Still not building
this now; recorded as a second, distinct technique the eventual
near-duplicate pass needs to cover, alongside pixel/perceptual hashing
for true bursts.

Update, same day: a fourth pair, `e1209ce2...` and `7c1dff32...`, back
in the pixel-burst category -- same Denali aurora shot (identical
mountain silhouette, tree line, star field, green-band shape),
almost certainly adjacent timelapse frames. Same signature as the
marmot and Wonder Lake pairs above (pHash/dHash territory), not the
embedding-similarity category the bus-road pair needs. Running total
so far: 4 catalog entries confirmed as true near-duplicate bursts
(marmot pair, Wonder Lake x4, aurora pair) plus 1 same-vantage-point/
different-content pair (bus road) -- all found by Josh's manual scan
of just the Denali set, all still unfixed pending the eventual dedup
pass.

Update, same day: a fifth, larger cluster -- 7 records, all dated
08/23/2017, all one ranger-led hike on Thorofare Ridge/Eielson Alpine
Trail: `119766ef...` (portrait crop, hikers descending), `a6b2cfb0...`
and `04af4095...` (byte-identical *thumbnails*, from two different
near-identical full-res source files -- same signature as the marmot
pair: group stopped, ranger gesturing), `416fd47c...` (same valley, no
hikers), `eb0a562f...` and `5acde91f...` (another byte-identical-
thumbnail pair, group descending), and `f7292bb8...` (near-identical
framing to that last pair, not byte-identical). One photo shoot
produced 7 separate published catalog entries.

This tips the scale on the earlier "log it, don't fix mid-scrape"
call -- 5 clusters found in a single manual scan of one park, the
last one alone accounting for 7 entries, is no longer an isolated-
edge-case rate. Flagged to Josh directly; his call on whether to keep
logging until his scan finishes or start the dedup pass now. Not
started as of this entry.

## 2026-09-03: sync loop crashed once with KeyError on license_confidence -- suspected write race, unresolved

Two consecutive publish cycles failed in a row: at 20:40, `git push`
was rejected because Josh had pushed a README commit directly (a real
divergence, not a bug -- fixed with a plain merge, no conflicts, files
didn't overlap). At 21:11, the next cycle's `build_site()` call itself
crashed with `KeyError: 'license_confidence'` on some catalog record --
a real code-level failure, not a per-image warning, and it meant the
site didn't publish for over 30 minutes (`sync_and_publish.py` retries
next cycle, so nothing was lost, but it went unpublished until caught
here by a routine health check).

Investigated: re-checking `data/catalog.json` immediately after found
zero records missing `license_confidence` -- whatever record triggered
it is no longer in that state. `license_confidence` is a pure
model-judged field (`pipeline.build_record()` merges it in via
`**model_fields` from `judge_image()`'s grammar-constrained output,
which always includes it on success), so no code path was found that
would legitimately omit it from a record that gets written at all.

Leading theory, not confirmed: `pipeline.py`'s `out_path.write_text
(json.dumps(records, indent=2))` rewrites the *entire* `catalog.json`
file non-atomically (no write-to-temp-then-rename) every time a record
is appended, while the scraper process that does this is running
concurrently with `build_site.py`'s own read of the same file (over
SSH, on the same host, no file lock between them). A read landing
mid-write could plausibly produce a malformed record. Doesn't fully
fit the evidence, though -- a truncated read would more typically
raise a JSON decode error than parse cleanly with one specific key
missing from one specific object; this is flagged as the most likely
explanation, not a confirmed root cause.

Not fixed -- one-off so far (single occurrence in days of continuous
operation), self-resolved by the next successful build. If it recurs,
the fix is almost certainly making `pipeline.py`'s catalog writes
atomic (write to a temp file, then `os.replace()`), which would close
the read-race window regardless of whether that's the exact mechanism
here. Noted as a real, if rare, gap the scrape isn't fully hardened
against.

## 2026-09-04: local-only duplicate review tool -- perceptual hashing tried and shelved, EXIF timestamp clustering used instead

Context: continuing to manually browse Denali (see the 2026-09-03
dedup-cluster entries above), Josh wanted a proper tool instead of
one-off findings -- clusters presented visually, an automatic-but-
reversible first pass for exact duplicates, and everything else left
to human judgment via a temporary local server, never the public site.

**Perceptual hashing (phash/ahash/dhash/whash), tried first and
rejected.** Tested against the known calibration set from the prior
DECISIONS.md entries (the 4-photo Wonder Lake sunset burst, the aurora
pair, and the confirmed-non-duplicate `2b8a099d...`): no algorithm
cleanly separated true duplicates from genuinely different shots. The
aurora pair -- visually near-identical -- scored 20-44 bits apart (of
64) on every algorithm, almost certainly because the moving aurora and
shifting stars dominate the hash and swamp the stable mountain-
silhouette signal that actually indicates "same shot." Average-hash
did reasonably on the Wonder Lake burst (2-11 bits) but put the known
non-duplicate at a comparably close distance (6 bits) to a real
member, so no fixed threshold would have worked reliably.

**EXIF timestamp clustering, used instead.** Josh's own suggestion:
group by park + capture time within a few minutes, using real EXIF
(rejecting the same clock-never-set sentinel dates `exif_util.py`
already guards against for `time_of_day`), rather than a visual
comparison at all. Required extending `exif_util.py` with
`exif_capture_datetime()` (same SubIFD-first, sentinel-rejecting
priority as the existing `exif_capture_hour()`, but minute-precision
instead of just an hour bucket). Tested against the same calibration
set: correctly clustered the Wonder Lake burst (including
`2b8a099d...`, appropriately not auto-hidden -- same session, still a
real judgment call), correctly caught the aurora pair pHash had
completely missed, and correctly grouped the whole 8-photo Thorofare
Ridge ranger-hike cluster. Full corpus (4,299 published records):
17 exact-duplicate clusters, 408 timestamp clusters at a 15-minute
gap threshold.

Decision: `scripts/find_duplicates.py` runs two clustering passes -- exact md5 match on thumbnails (auto-hides every member but the highest-`aesthetic_score` one, zero ambiguity) and EXIF-timestamp-within-15min-per-park (surfaced for manual review only, never auto-hidden, since "same session" still contains real, distinct photos as often as true duplicates). `scripts/dedup_review_server.py` is a local-only Flask app (binds to 127.0.0.1, never deployed) showing clusters as thumbnail grids with click-to-toggle keep/hide, persisting to `hidden_ids.json`
Alternatives-considered: visual near-duplicate detection via phash/ahash/dhash/whash (rejected -- doesn't separate the known test cases); CLIP-embedding similarity (not attempted -- needs the heavy torch/transformers stack this dev VM doesn't have installed; still a candidate for a future pass, e.g. for same-vantage-different-subject cases like the Denali tour-bus pair, which neither hashing nor timestamp clustering would catch since they're likely taken well apart in time)
Rationale: EXIF timestamps are a hard fact when present (unlike a pixel-similarity heuristic that has to somehow ignore scene motion), and burst/reprocessed-reupload duplicates are almost always captured minutes apart by the same photographer -- this reuses evidence the pipeline already extracts for a different purpose (`time_of_day`) rather than inventing a new, unreliable signal
Outcome: resolved for now. `hidden_ids.json` lives at the repo root (not under the gitignored `/data/`) since it's a real, hand-curated decision file, not regeneratable scrape output -- same treatment as `album_keywords.json`/`schema.json`. `build_site.py` does not yet read/respect it (open follow-up, not done this session) -- also open: `hidden_ids.json` needs to reach wopr somehow for that exclusion to take effect there, since `build_site()` runs on wopr over SSH.

## 2026-09-05: underrepresented-park plan -- floor/publish mismatch found and fixed, remaining thinness is genuine

Context: with the 61-park scrape finished, Josh asked for real per-park
published counts and noticed several parks published far below
`select_by_threshold_with_floor()`'s floor=10 guarantee. Investigated
before touching anything, since "some parks are just thin" and "the
floor isn't reaching the site" are different problems needing
different fixes.

**Root cause A -- floor guarantee never reached the site (a real bug).**
`curate.py`'s floor logic deliberately tops a park up to 10 candidates
with its next-best scorers *even below the 5.40 threshold* -- that's
the whole point of a floor. But `build_site.py`'s
`PUBLISH_MIN_AESTHETIC_SCORE` (5.4) applies the same cutoff at publish
time with no memory of which candidates were floor-exceptions.
Confirmed live on Badlands: of 13 candidates classified `landscape`,
6 scored 5.19-5.38 -- selected only to satisfy the floor, then
silently stripped back out at publish. The site never saw the floor
Josh had already decided he wanted.

**Root cause B -- some parks are genuinely thin under a landscape-only
site (not a bug).** Checked subject-mix directly: Wind Cave (8/80
`landscape`, rest `detail`/`wildlife`/`human_activity` -- it's a cave,
most of its NPS photography is underground rock formations, not
outdoor vistas), Dry Tortugas (2/19 -- mostly the fort itself,
`structure`, plus wildlife), Mesa Verde (5/13 -- mostly the cliff
dwellings, `structure`). These parks' defining feature isn't a
landscape vista at all; a strictly landscape-only policy will always
be thin here regardless of floor/threshold mechanics.

Decision: four-part remediation, in order, each step only applied where the prior step left a park still under 10 published:
1. Full 61-park measurement pass (selected count vs. published count vs. `primary_subject` mix) to find every affected park, not just the ones spot-checked in conversation.
2. Fix root cause A: at publish time, per park, if the standard >=5.4 cut leaves a park under floor (10), relax that park's cutoff to 5.2 (not lower, and not applied to parks already at/above floor) -- publish-time-only logic in `build_site.py`, no change to `curate.py`/selection, no new persisted field.
3. Re-measure. For parks still under 10, this is root cause B territory -- decide per park whether `structure` should be included (Mesa Verde/Dry Tortugas/Wind Cave-shaped cases, where it's the park's actual defining feature) rather than changing the site-inclusion policy globally.
4. For parks still under 10 with only a handful of albums total, manually re-check the albums `album_triage.classify_album()` excluded -- a blunt keyword match can throw out an album that has one genuinely scenic photo alongside mostly administrative content; not worth doing for a 40-album park, cheap for a 5-album one.
Explicit stopping condition (Josh): if a park is still under 10 after all four steps, it stays that way -- no further threshold relaxation, no forcing candidates that don't exist.
Alternatives-considered: persist a floor-inclusion flag on selection and thread it through to publish-time exemption (more mechanically correct but needs a schema/pipeline change for a problem publish-time-only logic already solves); remove `PUBLISH_MIN_AESTHETIC_SCORE` entirely (loses the floor concept's intent of "still a cut, just a lower one for thin parks"); globally allow `structure` (rejected in the original 2026-08-30 site-inclusion decision for lack of a good-vs-boring-structure signal, and still true today -- scoping it to specific named parks avoids that unsolved general problem)
Outcome: resolved. `build_site.py` gained `THIN_PARK_FLOOR`/`THIN_PARK_RELAXED_SCORE` (per-park relaxed cutoff) and `STRUCTURE_ALLOWED_PARKS` (Gateway Arch, Mesa Verde, Dry Tortugas, Virgin Islands -- each spot-checked by hand-viewing actual top-scoring `structure` candidates before allowlisting; Great Basin's were research-equipment photos and cave parks' `detail` candidates were an unreliable mix, neither earned inclusion). `scripts/_add_album_to_park.py` (new, one-off) added Carlsbad Caverns' "Historic Photograph Collection" album (314 candidates, wrongly excluded by an incidental "staff" keyword match) at the relaxed 5.2 threshold -- 55 records added (12 `landscape`, 7 `structure`, 17 `human_activity`, 18 `vehicle`, 1 `document`), confirming real scenic content had been sitting there since the original scrape.

Final published counts, 10 originally-thin parks (before -> after): Biscayne 1->2, Dry Tortugas 1->6, Congaree 2->4, Gateway Arch 2->102, Mesa Verde 5->12, Virgin Islands 5->9, Badlands 7->12, Carlsbad Caverns 8->15, Wind Cave 8->8, Great Basin 9->9. 4 of 10 (Gateway Arch, Mesa Verde, Badlands, Carlsbad Caverns) now clear floor=10. The remaining 6 stay below floor per the explicit stopping condition -- Biscayne/Wind Cave/Great Basin are genuine subject-mismatch cases already investigated and rejected for further inclusion; Dry Tortugas/Congaree/Virgin Islands improved but didn't fully clear 10 and weren't chased further.

Bug found and fixed mid-execution: the first run of `_add_album_to_park.py` added zero records -- every one of the 55 threshold-clearing candidates failed with "WOPR_BASE_URL is not set", because the script called `pipeline.build_record()` (which needs the VLM endpoint) without ever calling `load_dotenv()` first, unlike `pipeline.py`/`run_curated_scrape_remote.py`, which both do this explicitly at startup. The 314-candidate throttled thumbnail fetch had already completed and was cached, so nothing was lost -- fixed by adding the missing `load_dotenv(.env)` call, removing the 55 resulting `processing_error` checkpoint lines (so they'd retry instead of being treated as already-attempted), and re-running.

## 2026-09-05: `STRUCTURE_ALLOWED_PARKS` reverted -- back to `primary_subject == "landscape"` uniformly

Context: after `STRUCTURE_ALLOWED_PARKS` went live (previous entry, same day), Josh looked at the actual published site with the four parks' `structure` records live -- 116 photos of Gateway Arch, Mesa Verde's cliff dwellings, Fort Jefferson, and Virgin Islands' colonial ruins. His call: individually beautiful photos, but they don't fit the site's stated goal ("open-access *landscape* photography") and don't sit well visually alongside the rest of the gallery. That also surfaced a process problem: hand-picking a per-park subject exception every time a park runs short erodes trusting `primary_subject`'s classification uniformly -- the exact kind of case-by-case carve-out the original 2026-08-30 site-inclusion decision was trying to avoid by picking one subject value and sticking to it.

Decision: remove `STRUCTURE_ALLOWED_PARKS` entirely. `build_site.py` is back to a single inclusion rule -- `primary_subject == "landscape"`, no per-park exceptions -- for every park, including the four that had been allowlisted. `THIN_PARK_FLOOR`/`THIN_PARK_RELAXED_SCORE` (root-cause-A fix, same 2026-09-05 remediation) stay in place unchanged -- a thin park still gets the relaxed 5.2 cutoff and, per the original plan, a manual re-scan of excluded albums; it just no longer gets a subject-matter carve-out on top of that. As before, this is a display-gate change only -- the `structure` records themselves stay in `data/catalog.json` untouched, not deleted, and will reappear if a future policy change (see below) makes them eligible again.

Alternatives-considered: keep the allowlist but restrict it further (e.g. only the single most defining structure per park) -- rejected, doesn't solve the underlying "trust the classification uniformly" problem, just narrows the same kind of exception; add a numeric "structure quality" score and threshold it like aesthetic_score -- deferred, real fix but needs new inference work (see below), not a same-day change.

Real fix, deferred to the roadmap: add `structure_present`/`structure_prominence` fields to the inference grammar (`schema.json`) and site filter, so a genuinely striking structure shot (the Gateway Arch itself, Cliff Palace) can be surfaced deliberately as its own filterable category rather than smuggled in under `landscape` or excluded outright. Josh does not want a full re-scan of the existing corpus for this -- he plans to do a manual visual pass himself at some point instead. See `ROADMAP.md`.

Outcome: `STRUCTURE_ALLOWED_PARKS` and its structure-inclusion branch removed from `build_site.py`; corresponding tests (`test_build_site_includes_structure_for_allowlisted_parks`, `test_build_site_excludes_structure_for_other_parks`) replaced with a single `test_build_site_excludes_structure_uniformly`. Rebuilt and republished -- the 116 `structure` records (100 Gateway Arch, 7 Mesa Verde, 5 Dry Tortugas, 4 Virgin Islands) drop out of `docs/data.json`; those four parks fall back below floor=10 (Gateway Arch and Mesa Verde had cleared it only because of `structure`), which is accepted as the correct, expected outcome of this reversal -- not a new instance of the underrepresented-park problem to chase.

## 2026-09-06: NPGallery license/rights parsing investigation -- the real rule, and several rejected shortcuts

Context: the 2026-09-05 licensing rundown found 923 published records (16.4%) with a non-"Public domain/Full" license and, lacking a clean rule, built `license_review_server.py` for per-record human review, gated behind a new default-excluded `license_approved_ids.json` allowlist in `build_site.py`. Before using that tool for its intended purpose, Josh manually investigated ~16 real NPGallery asset pages by hand (URLs in the originating prompt) specifically to find out whether a real rule existed under the noise, rather than trusting per-record review to be the only option. It did.

**The rule:** NPGallery's asset metadata has three fields that are easy to conflate. Only one is a reliable reuse-rights signal:
- `Constraints Information` -- **authoritative.** Its *prefix* (ignoring everything after the first segment) is either "Public domain" or "Restrictions apply on use and/or reproduction" (with or without a "(Copyrighted material)" qualifier). This prefix alone determines the verdict.
- `Copyright` -- **not reliable alone.** Ranges from sitewide generic boilerplate (three distinct variants confirmed real, see `nps_client.COPYRIGHT_BOILERPLATE`) that appears across every verdict bucket including plainly copyrighted assets, to a bare name, to a fully explanatory paragraph. Worth preserving verbatim when it's not boilerplate, but never worth branching logic on.
- `Access Constraints` -- governs whether the catalog *record* is publicly searchable at all, not reuse rights. Confirmed uniformly `"Public Can View"` across all 923 records checked (via `scripts/fetch_license_review_details.py`'s AssetDetail-page scrape) -- it never varies on anything reachable through search/album APIs (restricted records presumably don't surface there), so it needed no schema field, just this confirmation.

**Decision:** `nps_client.is_open_license(license_str)` returns `license_str.startswith("Public domain")` -- nothing else. GrantingRights (Full/Partial/Minimum/Unknown) is not part of the check at all.

**Rejected heuristics (tested against real counter-examples, not assumed):**
- *GrantingRights level as a reuse-rights signal* -- rejected. Confirmed both `Public domain:Partial/Minimum` (open, just sometimes resolution-capped -- see below) and `Restrictions apply...:Full` (a named photographer's fully-copyrighted work, "Full Granting Rights" notwithstanding) live. It tracks something else NPS records internally, not whether the public may reuse the asset.
- *Credit-line format as a public-domain proxy* -- rejected. `NPS Photo/Emily Mesner` (Denali) carries `Restrictions apply on use and/or reproduction (Copyrighted material):Full Granting Rights`, despite matching a confirmed-PD `NPS/Neal Herbert`-style credit letter-for-letter in format. Every asset needs its own Constraints Information check regardless of how official the credit line looks.
- *Album-level trust as a substitute for per-item checking* -- rejected. Capitol Reef's "Night Scenes" album mixes public-domain NPS photos with two different photographers' individually-copyrighted submissions in the same album. Album triage (`album_keywords.json`) is a free pre-filter for *relevance*, never a substitute for checking Constraints Information on every asset that survives it.
- *Photographer/PhotoCredit/Contacts as a rights signal generally* -- rejected; display/attribution only, never branched on.

**Bare "Restrictions apply..." (no "(Copyrighted material)" qualifier) is still Exclude, high confidence** -- confirmed on Acadia's Gleason glass-plate donor collection (`AssetDetail/cef6db08-e20b-4354-b269-104f80976769`), where no reason is stated anywhere on the page at all. But a bare prefix does NOT mean "no reason given" in general -- Denali's `AssetDetail/68ffd9be-8fb3-4f57-9eb6-da033a771735` is also bare-prefix but its `Copyright` field fully explains "educational use ONLY" terms. The prefix alone decides the verdict either way; `Copyright`/`Explanation` text (when present and non-boilerplate) is preserved for context, not re-litigated.

**What changed in code:**
- `nps_client.NPSCandidate` gained `copyright_note` (verbatim `ConstraintsInformation.Explanation`, or a non-boilerplate `Copyright` string -- Explanation wins when both exist, since it's the more specific of the two) and `original_width`/`original_height` (from the album search API's `FileInfo.Original` -- confirmed present on `search_album()` responses, absent from the generic keyword-search endpoint `search_park_scenic()`/`search_candidates()` use).
- `asset_to_candidate()`'s `photographer` no longer falls back to `Copyright` when `PhotoCredit` is empty -- a real bug found 2026-09-05: that boilerplate text was displaying as the photographer name on-site (`Tower Peak`, `Mirror image landscape`, others).
- `pipeline.build_record()` appends `candidate.copyright_note` to the model's `license_evidence` (`" | NPS: ..."`) rather than assigning it directly -- `license_evidence` is a required `model_fields` key spread in after the deterministic block, so a direct assignment would've been silently clobbered. This does mean `license_evidence` now mixes a model-sourced visual read with NPS's own deterministic rights text when both exist; documented here rather than introducing a new schema field, since the two are never in tension (one describes what's visible in the pixels, the other what NPS's metadata says about reuse) and reviewers want both in one place.
- `build_site.py`'s publish gate replaced entirely: `FULLY_OPEN_LICENSE`/`license_approved_ids.json` (2026-09-05, exact-match on "Public domain/Full" only, per-record allowlist for everything else) is gone, replaced by `is_open_license()`. `license_review_server.py` and `scripts/fetch_license_review_details.py` are kept (real, useful tooling and the AssetDetail-scrape data fed directly into this investigation) but are no longer wired into publishing -- the manual per-record review they supported is superseded by this deterministic rule for the Constraints Information question specifically. `license_approved_ids.json` (always empty -- nothing was ever approved through it) deleted.
- `pipeline.run()` gained two new pre-download checks, in order: `is_open_license()` (checkpoint outcome `"license_excluded"`) and a `FileInfo`-based resolution check when `original_width`/`original_height` are known (reuses the existing `"too_small"` outcome) -- both skip before spending bandwidth or a `judge_image()` call on a candidate that can never publish. The existing post-download pixel check (2026-09-05) stays as the fallback for search paths without `FileInfo`.
- `album_keywords.json` gained `"photo contest"`/`"photography contest"`/`"photo challenge"` (contest entrants keep their own copyright -- confirmed systematic, e.g. Bryce Canyon's "2023 Winter Photo Contest Finalist" album). `"repeat photography"`/`"repeat photo"` were already added 2026-09-05 for an unrelated reason (composite before/after images, not a licensing problem) and needed no change.

**Resolution-availability check -- a deliberate deviation from what was asked for.** The prompt proposed comparing metadata's `File Size (bytes)` against the actual downloaded file size (confirmed live: several `Public domain:Partial/Minimum Granting Rights` Mount Rainier assets report a multi-megabyte original in metadata while only serving a ~500x375, <130KB derivative). That data isn't exposed on the generic search endpoints, only via the heavier AssetDetail HTML page -- fetching that per-candidate would add a full extra request to every scrape candidate. Checking confirmed the album search API (`search_album()`, the primary curated-pipeline strategy) already exposes exact `FileInfo.Original.Width`/`Height` for free in the same response used to build the candidate in the first place -- a strictly more precise, zero-additional-cost signal than a byte-size mismatch heuristic. Used that instead where available; the existing post-download pixel check (2026-09-05) remains the fallback for the generic-search paths that don't expose `FileInfo`, and was already confirmed to catch every real resolution-capped example in the prompt (all well under the 1920px `MIN_WALLPAPER_LONG_EDGE` threshold).

**Spot-check against the existing dataset** (`is_open_license()` against currently-published `docs/data.json`, 5,614 records): 5,083 keep publishing (all "Public domain," any GrantingRights), 531 would newly stop (all "Restrictions apply...," any GrantingRights) -- Denali 250, Lassen Volcanic 123, Hawaii Volcanoes 45, Kenai Fjords 40, Channel Islands 29, and smaller counts elsewhere. This exactly matches the 2026-09-05 rundown's own "Restrictions apply" tally, confirming the rule produces no surprise edge cases beyond the clean prefix split. Not yet rebuilt/republished -- pending Josh's go-ahead, batched with whatever else is queued.

Alternatives-considered: keep the per-record `license_review_server.py` review as the primary mechanism and use this investigation only to pre-fill suggested defaults -- rejected once the prefix rule proved to have no real exceptions in the sample investigated; a deterministic rule beats reviewing 923 records by hand when the rule actually holds. Add a dedicated `access_constraints` schema field -- rejected, confirmed invariant, no field needed unless a counter-example ever turns up.

Outcome: resolved. `nps_client.is_open_license()` is the single source of truth for the licensing verdict. 8 new tests in `test_nps_client.py` using real field values from the investigated assets (not synthetic guesses).

## 2026-09-06 (same day): license moved from a publish gate to a site filter, default "Public domain"

Context: immediately after the investigation above landed, Josh asked for a site-side filter ("public domain | restricted | any") instead of hard-excluding restricted-license records at build time. This is a same-day revision of that entry's `build_site.py`/`pipeline.py` changes, not a new investigation -- the underlying rule (`is_open_license()`) is unchanged.

Decision: `build_site.py` no longer excludes on license at all -- every otherwise-eligible record (landscape, not 360, scored, not hidden) publishes regardless of license, tagged with a new deterministic `license_category` field (`"public_domain"` or `"restricted"`, from `is_open_license()`). `docs/index.html`/`app.js` gained a "License" filter (Public domain / Restricted / Any) alongside the existing park/time/people/color/orientation/tag filters, defaulting to "Public domain" via the HTML `selected` attribute -- a first-time visitor sees only open-access content by default, same practical outcome as the publish-gate version, but restricted content is still in `docs/data.json` and reachable, not thrown away. `pipeline.run()`'s pre-download `is_open_license()` skip (checkpoint outcome `"license_excluded"`) is reverted -- restricted candidates are cataloged the same as any other now, since they're legitimate content the site can show behind the filter, not dead weight.

Rationale: the publish-gate version and this version produce an identical *default* page for a visitor who never touches the filters, so nothing about the site's default open-access presentation actually changes. But the gate version permanently discarded 531 records' visibility with no path back short of a rebuild, for content that's completely legal to display given NPGallery's own terms (`Access Constraints: Public Can View` on every one of them, confirmed via the 923-record scrape earlier today) -- a site filter keeps that content reachable and transparently labeled (the lightbox already shows the verbatim `license` string) for a visitor who deliberately asks to see it, which fits the project's existing "images aren't independently verified, see the license note" posture (`README.md`, `TERMS_OF_USE.md`) better than silent exclusion does.

Outcome: `build_site.py`'s `is_open_license` import stays (used to compute `license_category`), the exclusion branch is gone. 2 tests in `test_build_site.py` updated from exclusion-assertions to tagging-assertions; the pipeline license-skip test replaced with one confirming restricted candidates flow through normally. Rebuilt and republished same day: 4,175 records (3,901 public domain, 274 restricted), down from 5,614 -- combined effect of this change plus the resolution filter and hidden_ids.json growth from the same day's remediation.

## 2026-09-06: `content_visible` model field -- catches blank/washed-out scans, future scrapes only

Context: the 2026-09-05 blank-scan finding (9 published records essentially blank white, found via a `tags`-text heuristic, all hidden manually) needed a real fix, not just a one-off hide. Josh asked whether this could go into the model grammar directly, and confirmed he wants future-scrapes-only, no backfill of the existing corpus (same treatment as the not-yet-built `structure_present`/`structure_prominence` idea in `ROADMAP.md`) -- re-running `judge_image()` across the whole published corpus to backfill has a real inference cost he explicitly declined.

Decision: new `content_visible: boolean` model field, right next to `is_photograph` in both the GBNF grammar and prompt (`model_client.py`) -- "is this frame blank/washed-out/too degraded to see anything," distinct from `is_photograph` ("is this a real photograph at all"; a badly faded scan is still a real photograph, just unusable). Added to `schema.json` as a `properties` entry but deliberately NOT added to `required` -- the existing corpus has no such field and never will unless a future backfill decision reverses this. `build_site.py` gates on `record.get("content_visible", True)`, so a missing key (every record scraped before today) defaults to publishable, and only a model-judged `false` on a newly-scraped record excludes it.

Alternatives-considered: backfill the full corpus now -- rejected, explicit cost/scope call by Josh, existing bad records stay a manual-review problem (`hidden_ids.json`), same as they were yesterday; an enum distinguishing blank vs. damaged vs. artifacted -- rejected as unnecessary complexity, the publish decision is binary (usable or not) regardless of which failure mode caused it.

Outcome: resolved for future scrapes. 4 new tests (`test_schema_validate.py` x2, `test_build_site.py` x2). No backfill; the 9 already-known blank records remain manually hidden via `hidden_ids.json`, and any others already in the corpus stay undiscovered until found by hand, same as before.

## 2026-09-06 (same day, third entry): license reverted back to a hard exclusion, site filter removed

Context: after living with the site-filter version (previous-but-one entry) for one rebuild cycle, Josh changed his mind and asked to go back to excluding restricted/copyrighted images entirely, from both the published site and pipeline intake.

Decision: `build_site.py`'s `eligible` filter has `is_open_license(r.get("license", ""))` again, same as the first 2026-09-06 entry. The `license_category` field is removed from published records (there would only ever be one value again, so nothing to tag) and the site's "License" filter control (`docs/index.html`, `docs/app.js`) is removed entirely rather than left in place always showing "Public domain" with no other real option. `pipeline.run()`'s pre-download `is_open_license()` skip (checkpoint outcome `"license_excluded"`) is restored, so restricted candidates go back to being skipped before any bandwidth/VLM cost, not cataloged.

Note this is a *third* reversal of the same axis in one day (923-per-record-review -> deterministic rule as a publish gate -> site filter -> back to publish gate) -- each step was a real, reasoned decision at the time, not churn for its own sake, but it's worth being honest that this landed back where the first same-day entry left it. The underlying rule itself (`nps_client.is_open_license()`, "Public domain" prefix only) never changed across any of these -- only what happens once a record fails it did.

Outcome: resolved, this time treated as the stable end state unless raised again. 2 tests in `test_build_site.py` reverted to exclusion-assertions, the pipeline license-skip test restored. Rebuilt and republished (see the following rebuild's numbers for the final count).

## 2026-09-06 (same day, fourth entry): license_review_server.py narrowed to flagged_for_review; "approve" was a no-op

Context: after adding the 359 `license_confidence == "flagged_for_review"` records to `license_review_server.py`'s candidate pool, Josh asked what a click there should actually do, since the pool now mixed several genuinely different situations. Checking the code surfaced a real bug: clicking "approved" wrote to `license_approved_ids.json`, a file nothing reads anymore -- that mechanism was deleted in the license-reverted-to-hard-exclusion entry above, so every prior click in this tool (before this fix) had zero effect on the published site.

Breakdown of what was actually in the 1,259-record candidate pool, and whether each still needs a decision:
1. License prefix "Restrictions apply..." (~531) -- hard-excluded, permanently, no override mechanism exists anymore (this is the point of the previous entry). Nothing to decide.
2. License "Public domain" with GrantingRights Partial/Unknown (~392) -- already correctly published; the 2026-09-06 investigation settled that GrantingRights isn't a real rights signal, so `is_open_license()` deliberately ignores it. Nothing to decide.
3. `license_confidence == "flagged_for_review"` on a record whose license passes `is_open_license()` (368 in the current corpus) -- the only real open question left: is the model's stated visual concern (watermark, embedded copyright notice, identifiable person) something to act on?

Decision: narrow `license_review_server.py` to serve only category 3. Every card shown is already live on the site, so the click semantics flip from the old tool's polarity: default state is "kept" (published, matches reality, no file write needed), and a click means "this flagged concern is real" -- writes the id straight into `hidden_ids.json`, the same file `dedup_review_server.py` uses and the one `build_site.py` actually reads. Click again to un-hide. No new state file, no separate "approved" concept -- reuses the one hiding mechanism that already works.

Alternatives-considered: a three-state model (unreviewed / kept / hidden) to track review progress explicitly -- rejected as unnecessary complexity for now; "not in hidden_ids.json" already means "kept," and Josh didn't ask for progress-tracking, just a working action. Keep categories 1/2 visible as read-only reference -- rejected in favor of dropping them entirely, since they add scroll/noise for zero remaining decisions.

Outcome: resolved. `license_approved_ids.json` deleted (unused). `api_decide` now writes to `hidden_ids.json` exactly like `dedup_review_server.py`'s endpoint of the same shape. Restarted; 368 flagged-and-published records shown.

## 2026-09-06 (same day, fifth entry): narrowed license_confidence prompt, new minor_face_present hard-exclude field, flagged_for_review publish gate

Context: Josh's own review of `license_confidence`/`license_evidence` output found the model flagging things with no copyright relevance at all -- recognizable people, an NPS logo on a sign, a brand logo on tent fabric -- and separately, a human reviewer had found and manually hidden an image of two minors (approx. 12-14) early in the scrape, which needed to become an automated, standing check rather than something caught only by chance.

**Verification pass first** (per instructions, before any implementation): two items believed already-implemented turned out not to match their description.
- Album-triage "photo contest"/"repeat photography" exclusion is real, but only checks the *album's* title/description -- there's no per-item check against an asset's own title/Keywords, so a case like Comerci's (no matching album name, only a `Keywords` hit) still isn't caught. Confirmed a real gap; Josh deferred fixing it (out of scope for this pass).
- The resolution-availability check does not compare `File Size (bytes)` metadata against actual download size as originally specified -- it uses `FileInfo.Original.Width`/`Height` from the album search API instead (a deliberate 2026-09-06 design choice, see that entry), which is strictly more precise and costs zero extra requests. Josh confirmed: keep the existing mechanism, don't add the byte-comparison as well.
- Confirmed clean: `copyright_note`→`license_evidence` wiring works as specified; no rejected heuristic (GrantingRights, credit-line format, album trust) is used anywhere as a PD signal; `3ef54278-2415-4751-922c-47b88025b6db` (the Comerci watermark case) is in `hidden_ids.json`.

**1. `license_confidence` prompt narrowed.** Now asks only "does this image contain a visible copyright mark, watermark, or credit line printed on the photo itself," with an explicit instruction not to flag on people, brand logos, or signage/trademarks -- those are handled elsewhere (`people_prominence`) or aren't real copyright signals in this context at all. Validated against the 3 known false-positive cases from the old prompt (a ranger silhouette at Acadia, a silhouetted figure at Delicate Arch, a tent's brand logo) -- all 3 now come back `confirmed`, no regression.

**2. New `minor_face_present`/`minor_face_evidence` model fields -- hard exclude, not review-and-publish.** Deliberately the opposite polarity from `license_confidence`: a license flag holds a record pending human review; a minor-face flag excludes it from `build_site.py` immediately and unconditionally, no window where it could go live. Standing policy restated for clarity: people in images are included by default (`people_prominence` is a filter axis, not a reject gate) -- minors with a visible, recognizable face are the one deliberate exception. Optimized for recall over precision (a false positive costs one image from a large pool; a false negative is unacceptable) but scoped narrowly to an actually-visible face specifically, to avoid the failure mode the old broad `license_confidence` prompt already demonstrated (flagging a small silhouetted figure under Delicate Arch with no visible face at all) -- applied to minors, that same over-broad framing would gut a huge share of ordinary landscape shots with any small figure in them, for no real child-safety benefit. `TERMS_OF_USE.md` gained a plain statement of this exact policy (visible-face minors excluded; small/distant/no-visible-face figures are not).

Validation before rollout, per Josh's explicit requirement -- **not run across the dataset, tested against a 10-image human-picked set first**, results reported plainly without editorializing on correctness:

| id | minor_face_present | minor_face_evidence |
|---|---|---|
| 07c6877d... | false | no visible minor face |
| 1cd4a3e1... | false | no visible minor face |
| fdc0fcd3... | **true** | "A young child with light hair is clearly visible from the side in the foreground, face discernible and likely a minor." |
| 4348fb56... | false | no visible minor face |
| 796752b1... | false | no visible minor face |
| 632aa70a... | false | no visible minor face |
| 9f98c9b1... | false | no visible minor face |
| 4363a003... | false | no visible minor face |
| 944e07d7... | false | no visible minor face |
| 81abe315... | false | no visible minor face |

1 of 10 flagged, all 10 had `people_present: true` (so the model isn't just echoing that flag), and the one flag came with specific, checkable reasoning rather than a generic hit. Not independently judged for correctness here -- that's the point of running it as a held-out human review step before trusting it at scale.

**3. New publish gate: `license_confidence == "confirmed"` required.** Previously nothing gated on this field at all -- 359 of 3,901 published records (9%) were live with an unreviewed flag. Same gate also excludes `minor_face_present == true`. **Real consequence found while implementing this, surfaced before any rebuild**: of the 359 currently-published flagged records, only 8 are in `hidden_ids.json` (Josh's own review decisions so far) -- the other 351 have been implicitly kept published by *not* being hidden, but nothing has ever set their `license_confidence` to `"confirmed"`, and no tool exists to do that. Once this gate ships and a rebuild runs, all 351 will disappear from the site regardless of whether Josh already reviewed and approved them -- there is currently no path back except manually editing `data/catalog.json` or building a "mark confirmed" action into `license_review_server.py`. Flagged for Josh's decision, not resolved unilaterally here.

**Resolved:** Josh chose to add a `confirmed_ids.json` override file (same pattern as `hidden_ids.json`, opposite polarity -- an allowlist, not a denylist) and a matching "Confirm" action in `license_review_server.py`, then bulk-confirm the 351 as a one-time migration of the pre-gate status quo. `build_site.py`'s gate became `license_confidence == "confirmed" or id in confirmed_ids`. The review tool now tracks three real states per flagged record -- unreviewed and hidden currently produce the same publish outcome (not published) but mean different things (an open question vs. a reviewed rejection); confirmed is the only state that overrides the flag. Going forward, a *newly* flagged record starts unreviewed, not auto-confirmed -- only this specific backlog got grandfathered in, as an explicit one-time call, not a standing default.

Outcome: resolved and deployed. Prompt narrowing and `minor_face_present` are implemented and validated on the held-out set; not yet run across the dataset (future scrapes only, no backfill, same as `content_visible`). Publish gate, `confirmed_ids.json` mechanism, and the bulk-confirm migration are all implemented, tested (5 new tests in `test_build_site.py` total), and deployed to wopr. `license_review_server.py` restarted with the three-state Confirm/Hide UI -- 351 confirmed, 9 unreviewed (found since the 359 count was taken), 8 hidden.
