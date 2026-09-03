# Setup

## Prerequisites

Before running anything beyond the test suite, you need a local vision-language model reachable over an **OpenAI-compatible endpoint**. The classification step (`model_client.py`) calls this endpoint for every image — there's no cloud fallback, and `uv run vistarium` will fail once it reaches that stage without one.

This project's own instance runs a VLM served via `llama.cpp`/`llama-swap` on a home inference box (Tesla V100). Yours doesn't need to match that hardware — any server exposing an OpenAI-compatible `/v1/chat/completions` endpoint that accepts image input works — but you do need *something* there. See [`ARCHITECTURE.md`](./ARCHITECTURE.md) for what the model is actually asked to do and why it's kept to narrow, structured-output judgment calls.

Configure the endpoint in `.env` (see `.env.example`).

## Install

Requires [`uv`](https://docs.astral.sh/uv/).

```bash
uv sync                 # installs pinned deps from uv.lock
cp .env.example .env    # set your model endpoint here (see Prerequisites above)
uv run pytest           # run the test suite — this part works with no model endpoint
uv run ruff check .     # lint
uv run ruff format .    # format
```

## Run the pipeline

```bash
uv run vistarium --limit 20
```

Writes `data/catalog.json` (schema-valid photograph records) and `data/excluded_non_photo.json` (images the model flagged as not photographs — kept for audit, not shown on the site).

This is the step that needs the model endpoint from Prerequisites. Everything before it (`nps_client.py`, dedup, EXIF reading) runs without one.

## Build the static site

```bash
uv run vistarium-build-site
```

Writes `docs/data.json` and `docs/thumbs/*.webp` from `data/catalog.json`, filtered to `primary_subject: landscape`. `docs/` is what GitHub Pages serves. No model endpoint needed for this step — it only reads an existing catalog.

## Optional: aesthetic scoring

```bash
uv sync --extra aesthetic
uv run vistarium-score-aesthetics
```

A separate, optional dependency (`torch`/`transformers`, ~1–2GB) — not required for the steps above. See [`ARCHITECTURE.md`](./ARCHITECTURE.md#aesthetic-scoring) for what this does and why it's kept separate.
