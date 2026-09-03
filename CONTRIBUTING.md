# Contributing

Vistarium is open source, but not open to contribution.

The code is [MIT-licensed](./LICENSE) — fork it, adapt it, build on it, reuse the pipeline for a different archive, whatever's useful to you. That's what the license is for.

What this project doesn't do is accept outside pull requests.

**The main reason is concrete, not just preference: this pipeline is coupled to a specific local setup.** The classification step calls a local vision model over an OpenAI-compatible endpoint served from a home inference box (a Tesla V100, via `llama.cpp`/`llama-swap`, on an Unraid dev VM). To actually run this project end-to-end — not just read the code, but reproduce its output — you need equivalent local inference infrastructure serving a comparable model. I can't meaningfully review a PR touching `model_client.py`, the curation thresholds, or anything downstream of the model's output when I have no way to verify it against what a contributor's hardware actually produced. This isn't "send a PR to a GitHub Actions CI runner and I'll check the diff" — the whole point of the architecture is that the expensive judgment step runs somewhere I control and can trust.

Two smaller reasons on top of that:

- **It's actively changing.** The schema, pipeline stages, and even the model backing classification are still in motion. Reviewing external PRs against a moving target isn't a good use of either of our time.
- **It's part of how I'm learning this.** Working through the design decisions myself, including the wrong turns, is the point.

## What you can do instead

- **Fork it — with the caveat above in mind.** The deterministic parts (scraper, dedup, crop math, site builder, album triage) are genuinely portable and useful on their own. The classification step will need your own inference endpoint wired in via `model_client.py`, or swapped for a cloud API call, before it does anything. MIT-licensed either way — no permission needed.
- **Open an Issue** for bugs, questions, or things that seem broken. I do read these, even though I won't be opening a PR review queue.
- **Don't open a PR.** It won't be reviewed or merged, however good it is — this isn't a reflection on the contribution, just a reflection of the constraints above.
