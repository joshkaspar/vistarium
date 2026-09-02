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
