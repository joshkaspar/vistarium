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
