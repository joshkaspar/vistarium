Agent Policy: Decision Logging & Commit Discipline
---

Purpose: make every future commit self-sufficient for explaining "why,"
without adding overhead to routine work. Applies to any agent (Claude
Code or a human) committing to this repo. This is this project's own
take on the standard "architecture decision record" (ADR) pattern --
see `DECISIONS.md`, 2026-08-29, for the maintainability requirements
that established it.

## 1. Two classes of commit

Mechanical -- typo fixes, dependency bumps, refactors with no behavior
change, config tweaks that don't reflect a choice between alternatives.
Normal one-line commit message. No further action.

Decision -- anything where a choice was made between real alternatives,
a root cause was found after a wrong turn, a design constraint was
discovered, or a plan changed direction. These get the structured
trailer below AND an entry in `DECISIONS.md`.

Rule of thumb: if a future reader would want to know "why," it's a
decision commit. If it would just show a diff, it's mechanical.

## 2. Decision commit format

```
<subject line as normal>

<one paragraph: what was tried, what was learned, what changed>

Decision: <the choice made, one line>
Alternatives-considered: <other options, comma-separated, or "none -- first attempt">
Rationale: <why this one, one line>
Outcome: <resolved | open-issue | superseded-by <hash>>
```

Example:

```
Reject 9-way crop_anchor, keep 5-way

Tested adding 4 diagonal corners (topleft/topright/bottomleft/
bottomright) to crop_anchor against 79 real images. Corners were used
rarely (6/79) but, when used, tracked the single brightest point in
frame (sun glare, a bright star) rather than the actual photographic
subject, in 2 of the 6 cases checked visually.

Decision: crop_anchor stays 5-way (center/top/bottom/left/right)
Alternatives-considered: 9-way with diagonal corners, raw pixel crop box
Rationale: the corner tier's failure mode (tracking brightness, not subject) is worse than the coarseness it was meant to fix
Outcome: resolved
```

## 3. `DECISIONS.md` (append-only)

Every Decision commit gets a matching entry, newest at bottom:

```
## YYYY-MM-DD -- <short title>
Commit: <hash>
<2-4 sentences: context, what was chosen, why. Plain prose, not trailers.>
```

## 4. Attribution

When an agent proposes the Decision/Rationale content, prefix the
`DECISIONS.md` entry with `[agent-drafted, Josh-approved]` if Josh
reviewed and accepted it as written, or note what Josh changed if he
edited it.

## 5. Session summaries for multi-commit work

For an agent session that produces several commits toward one goal, end
the session by appending one `DECISIONS.md` entry summarizing the arc,
even if individual commits already have trailers.

## 6. What NOT to do

* Don't add Decision trailers to mechanical commits -- noise defeats the purpose.
* Don't let an agent silently summarize away a wrong turn -- a rejected approach gets its own Decision entry with `Outcome: superseded-by <hash>`, not silent deletion from history.
* Don't rewrite past commit messages to retrofit this format -- apply going forward.
