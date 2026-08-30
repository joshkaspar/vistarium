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
validated for this exact task family twice over in the `wopr` repo: a
head-to-head vision-accuracy eval against `qwen2.5-vl-32b`/`qwen-vl`
(2026-08-17, listing-scanner project) and a 5-model side-by-side on the
direct predecessor of this project (`pdscan-books`, 2026-08-21), where
it was the only model that ever actually opened and visually inspected
an image before judging it. A follow-up smoke test (79 real images, see
below) confirmed grammar-constrained structured output works reliably
against it.

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

Also found, unrelated to any of the above: one real NPS source file
(`pdscan-landscapes-qwen38-v2/images/evening/06_nps_...jpg`) has a
`.jpg` extension but is actually TIFF-encoded data. PIL's content-based
sniffing handled it fine in the pipeline; logged in `ROADMAP.md` as a
possible future hardening item, not fixed now since it's a single
known instance so far.

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
the smoke-test scripts in `wopr/model_tests/` were already fixed
earlier in this project.

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
